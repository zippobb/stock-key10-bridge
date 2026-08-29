import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts


BASE_DIR = Path("/opt/stock-automation/scanner")
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount"
)
BASIC_FIELDS = (
    "ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,total_mv,circ_mv"
)
STOCK_FIELDS = "ts_code,symbol,name,industry,market,exchange,list_date"


def classify_segment(ts_code: str) -> str:
    code, exchange = ts_code.split(".")
    if exchange == "SH" and code.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if exchange == "SZ" and code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if exchange == "SZ" and code.startswith(("300", "301")):
        return "创业板"
    if exchange == "SH" and code.startswith(("688", "689")):
        return "科创板"
    return "排除市场"


def get_latest_daily(pro):
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    start_date = (now - timedelta(days=30)).strftime("%Y%m%d")
    end_date = now.strftime("%Y%m%d")
    calendar = pro.trade_cal(
        exchange="SSE",
        start_date=start_date,
        end_date=end_date,
        is_open="1",
        fields="cal_date,is_open",
    )
    if calendar.empty:
        raise RuntimeError("交易日历为空，无法识别最新交易日")

    for trade_date in sorted(calendar["cal_date"].astype(str), reverse=True):
        daily = pro.daily(trade_date=trade_date, fields=DAILY_FIELDS)
        if len(daily) >= 1000:
            return trade_date, daily

    raise RuntimeError("最近30天没有找到完整的A股日线数据")


def main() -> None:
    token = <REDACTED>
    if not token:
        raise RuntimeError("缺少环境变量 TUSHARE_TOKEN")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    pro = ts.pro_api(token)
    trade_date, daily = get_latest_daily(pro)
    basic = pro.daily_basic(trade_date=trade_date, fields=BASIC_FIELDS)
    stocks = pro.stock_basic(
        exchange="",
        list_status="L",
        fields=STOCK_FIELDS,
    )

    if basic.empty:
        raise RuntimeError(f"{trade_date} 的 daily_basic 数据为空")
    if stocks.empty:
        raise RuntimeError("stock_basic 上市股票名单为空")
    if daily["ts_code"].duplicated().any():
        raise RuntimeError("daily 出现重复股票代码")
    if basic["ts_code"].duplicated().any():
        raise RuntimeError("daily_basic 出现重复股票代码")

    merged = (
        daily.merge(basic, on=["ts_code", "trade_date"], how="left")
        .merge(stocks, on="ts_code", how="left")
    )

    merged["segment"] = merged["ts_code"].map(classify_segment)
    merged["risk_name"] = merged["name"].fillna("").str.contains(
        r"ST|退", case=False, regex=True
    )
    merged["amount_yi"] = merged["amount"] / 100000
    merged["total_mv_yi"] = merged["total_mv"] / 10000
    merged["circ_mv_yi"] = merged["circ_mv"] / 10000

    universe = merged.loc[
        (merged["segment"] != "排除市场") & (~merged["risk_name"])
    ].copy()
    universe = universe.sort_values(
        ["segment", "total_mv_yi"], ascending=[True, False]
    )

    raw_path = DATA_DIR / f"market_raw_{trade_date}.csv"
    universe_path = DATA_DIR / f"base_universe_{trade_date}.csv"
    latest_path = OUTPUT_DIR / "base_universe_latest.csv"
    summary_path = OUTPUT_DIR / f"run_summary_{trade_date}.json"

    merged.to_csv(raw_path, index=False, encoding="utf-8-sig")
    universe.to_csv(universe_path, index=False, encoding="utf-8-sig")
    universe.to_csv(latest_path, index=False, encoding="utf-8-sig")

    summary = {
        "run_time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "daily_rows": int(len(daily)),
        "daily_basic_rows": int(len(basic)),
        "listed_stocks": int(len(stocks)),
        "merged_rows": int(len(merged)),
        "missing_total_mv": int(merged["total_mv"].isna().sum()),
        "excluded_market": int((merged["segment"] == "排除市场").sum()),
        "excluded_risk_name": int(
            ((merged["segment"] != "排除市场") & merged["risk_name"]).sum()
        ),
        "final_universe": int(len(universe)),
        "segment_counts": {
            str(key): int(value)
            for key, value in universe["segment"].value_counts().items()
        },
        "files": {
            "raw": str(raw_path),
            "universe": str(universe_path),
            "latest": str(latest_path),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n基础股票池前5行：")
    print(
        universe[
            [
                "ts_code",
                "name",
                "segment",
                "close",
                "pct_chg",
                "amount_yi",
                "turnover_rate",
                "total_mv_yi",
            ]
        ]
        .head(5)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
