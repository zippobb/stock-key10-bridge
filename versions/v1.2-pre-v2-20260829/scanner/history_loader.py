import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts


BASE_DIR = Path("/opt/stock-automation/scanner")
DATA_DIR = BASE_DIR / "data"
HISTORY_DIR = DATA_DIR / "history_daily"
OUTPUT_DIR = BASE_DIR / "output"
UNIVERSE_PATH = OUTPUT_DIR / "base_universe_latest.csv"

DAILY_FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
REQUIRED_DAILY_COLUMNS = set(DAILY_FIELDS.split(","))


def parse_args():
    parser = argparse.ArgumentParser(description="回填并维护A股日线历史缓存")
    parser.add_argument("--days", type=int, default=300, help="交易日数量，默认300")
    parser.add_argument("--pause", type=float, default=0.8, help="接口调用间隔秒数")
    return parser.parse_args()


def validate_args(args):
    if not 1 <= args.days <= 400:
        raise ValueError("--days 必须在1到400之间")
    if not 0 <= args.pause <= 10:
        raise ValueError("--pause 必须在0到10之间")


def read_universe():
    if not UNIVERSE_PATH.exists():
        raise FileNotFoundError(f"基础股票池不存在：{UNIVERSE_PATH}")

    universe = pd.read_csv(UNIVERSE_PATH, dtype={"ts_code": str, "trade_date": str})
    required = {"ts_code", "trade_date"}
    missing = required - set(universe.columns)
    if missing:
        raise RuntimeError(f"基础股票池缺少字段：{sorted(missing)}")
    if universe.empty:
        raise RuntimeError("基础股票池为空")
    if universe["ts_code"].duplicated().any():
        raise RuntimeError("基础股票池存在重复股票代码")

    trade_dates = universe["trade_date"].dropna().astype(str).unique()
    if len(trade_dates) != 1:
        raise RuntimeError(f"基础股票池交易日不唯一：{trade_dates.tolist()}")

    anchor_date = trade_dates[0]
    if len(anchor_date) != 8 or not anchor_date.isdigit():
        raise RuntimeError(f"基础股票池交易日格式错误：{anchor_date}")

    return universe, anchor_date


def get_trade_dates(pro, anchor_date, days):
    anchor = datetime.strptime(anchor_date, "%Y%m%d")
    calendar_days = max(60, int(days * 1.8) + 30)
    start_date = (anchor - timedelta(days=calendar_days)).strftime("%Y%m%d")
    calendar = pro.trade_cal(
        exchange="SSE",
        start_date=start_date,
        end_date=anchor_date,
        is_open="1",
        fields="cal_date,is_open",
    )
    if calendar.empty:
        raise RuntimeError("交易日历为空")

    dates = sorted(calendar["cal_date"].astype(str).unique())
    if len(dates) < days:
        raise RuntimeError(f"交易日历仅返回{len(dates)}天，少于要求的{days}天")
    return dates[-days:]


def validate_daily(frame, trade_date, minimum_rows=1000):
    missing = REQUIRED_DAILY_COLUMNS - set(frame.columns)
    if missing:
        raise RuntimeError(f"{trade_date} 日线缺少字段：{sorted(missing)}")
    if len(frame) < minimum_rows:
        raise RuntimeError(f"{trade_date} 日线仅{len(frame)}行，疑似不完整")
    if frame["ts_code"].duplicated().any():
        raise RuntimeError(f"{trade_date} 日线存在重复股票代码")
    actual_dates = frame["trade_date"].dropna().astype(str).unique()
    if len(actual_dates) != 1 or actual_dates[0] != trade_date:
        raise RuntimeError(
            f"{trade_date} 日线日期不一致：{actual_dates.tolist()}"
        )


def fetch_daily(pro, trade_date, attempts=4):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            frame = pro.daily(trade_date=trade_date, fields=DAILY_FIELDS)
            validate_daily(frame, trade_date)
            return frame
        except Exception as exc:
            last_error = exc
            if attempt == attempts:
                break
            wait_seconds = 3 * (2 ** (attempt - 1))
            print(
                f"{trade_date} 第{attempt}次获取失败：{exc}；"
                f"{wait_seconds}秒后重试",
                flush=True,
            )
            time.sleep(wait_seconds)
    raise RuntimeError(f"{trade_date} 获取失败，已重试{attempts}次：{last_error}")


def write_csv_atomic(frame, path):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temp_path, index=False, encoding="utf-8-sig")
    os.replace(temp_path, path)


def load_or_fetch_date(pro, trade_date):
    cache_path = HISTORY_DIR / f"daily_{trade_date}.csv"
    if cache_path.exists():
        cached = pd.read_csv(
            cache_path,
            dtype={"ts_code": str, "trade_date": str},
        )
        validate_daily(cached, trade_date)
        return cached, True, cache_path

    frame = fetch_daily(pro, trade_date)
    frame = frame.sort_values("ts_code").reset_index(drop=True)
    write_csv_atomic(frame, cache_path)
    return frame, False, cache_path


def main():
    args = parse_args()
    validate_args(args)

    token = <REDACTED>
    if not token:
        raise RuntimeError("缺少环境变量 TUSHARE_TOKEN")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    universe, anchor_date = read_universe()
    universe_codes = set(universe["ts_code"].astype(str))
    pro = ts.pro_api(token)
    trade_dates = get_trade_dates(pro, anchor_date, args.days)

    filtered_frames = []
    date_stats = []
    fetched_dates = 0
    cached_dates = 0

    for index, trade_date in enumerate(trade_dates, start=1):
        frame, from_cache, cache_path = load_or_fetch_date(pro, trade_date)
        if from_cache:
            cached_dates += 1
            source = "缓存"
        else:
            fetched_dates += 1
            source = "接口"

        filtered = frame[frame["ts_code"].isin(universe_codes)].copy()
        filtered_frames.append(filtered)
        date_stats.append(
            {
                "trade_date": trade_date,
                "raw_rows": int(len(frame)),
                "universe_rows": int(len(filtered)),
                "source": source,
                "cache": str(cache_path),
            }
        )
        print(
            f"[{index}/{len(trade_dates)}] {trade_date} "
            f"raw={len(frame)} universe={len(filtered)} source={source}",
            flush=True,
        )
        if not from_cache and index < len(trade_dates) and args.pause:
            time.sleep(args.pause)

    history = pd.concat(filtered_frames, ignore_index=True)
    history = history.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise RuntimeError("合并历史数据存在重复的股票代码和交易日")

    latest_path = OUTPUT_DIR / "history_daily_latest.csv"
    write_csv_atomic(history, latest_path)

    coverage = [item["universe_rows"] for item in date_stats]
    summary = {
        "run_time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "anchor_date": anchor_date,
        "requested_trade_days": args.days,
        "first_trade_date": trade_dates[0],
        "last_trade_date": trade_dates[-1],
        "universe_stocks": int(len(universe_codes)),
        "history_rows": int(len(history)),
        "fetched_dates": fetched_dates,
        "cached_dates": cached_dates,
        "min_daily_universe_rows": int(min(coverage)),
        "max_daily_universe_rows": int(max(coverage)),
        "latest_file": str(latest_path),
        "date_stats": date_stats,
    }
    summary_path = OUTPUT_DIR / f"history_summary_{anchor_date}_{args.days}d.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n历史数据前5行：")
    print(history.head(5).to_string(index=False))


if __name__ == "__main__":
    main()
