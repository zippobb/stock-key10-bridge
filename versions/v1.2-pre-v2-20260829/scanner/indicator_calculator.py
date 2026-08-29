#!/usr/bin/env python3
"""Calculate objective technical indicators from the A-share history cache."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


HISTORY_REQUIRED = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算A股客观量价与阶段位置指标")
    parser.add_argument(
        "--history",
        default="output/history_daily_latest.csv",
        help="历史日线合并文件",
    )
    parser.add_argument(
        "--universe",
        default="output/base_universe_latest.csv",
        help="最新基础股票池文件",
    )
    parser.add_argument(
        "--output",
        default="output/indicators_latest.csv",
        help="最新指标输出文件",
    )
    parser.add_argument(
        "--snapshot-dir",
        default="data/indicators",
        help="按交易日保存指标快照的目录",
    )
    return parser.parse_args()


def require_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def rolling(grouped, column: str, window: int, operation: str) -> pd.Series:
    series = grouped[column]
    roller = series.rolling(window=window, min_periods=window)
    result = getattr(roller, operation)()
    return result.reset_index(level=0, drop=True)


def pct_change(grouped, column: str, periods: int) -> pd.Series:
    return grouped[column].pct_change(periods=periods, fill_method=None) * 100.0


def valid_flag(condition: pd.Series, valid: pd.Series) -> pd.Series:
    result = pd.Series(pd.NA, index=condition.index, dtype="Int8")
    result.loc[valid] = condition.loc[valid].astype("int8")
    return result


def calculate_indicators(history: pd.DataFrame) -> pd.DataFrame:
    df = history.copy()
    df = df.sort_values(["ts_code", "trade_date"], kind="stable").reset_index(drop=True)
    grouped = df.groupby("ts_code", sort=False, group_keys=False)

    df["history_days"] = grouped.cumcount() + 1

    for window in (5, 10, 20, 60, 120, 250):
        df[f"ma{window}"] = rolling(grouped, "close", window, "mean")

    for window in (5, 20):
        df[f"vol_ma{window}"] = rolling(grouped, "vol", window, "mean")
        df[f"amount_ma{window}"] = rolling(grouped, "amount", window, "mean")

    for window in (20, 60, 120):
        df[f"high_{window}d"] = rolling(grouped, "high", window, "max")
        df[f"low_{window}d"] = rolling(grouped, "low", window, "min")

    for periods in (5, 10, 20, 60):
        df[f"return_{periods}d_pct"] = pct_change(grouped, "close", periods)

    df["volatility_20d_pct"] = rolling(grouped, "pct_chg", 20, "std")

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - df["pre_close"]).abs(),
            (df["low"] - df["pre_close"]).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["true_range"] = true_range
    grouped = df.groupby("ts_code", sort=False, group_keys=False)
    df["atr14_pct"] = rolling(grouped, "true_range", 14, "mean") / df["close"] * 100.0

    df["volume_ratio_1d_20d"] = df["vol"] / df["vol_ma20"]
    df["volume_ratio_5d_20d"] = df["vol_ma5"] / df["vol_ma20"]
    df["amount_ratio_1d_20d"] = df["amount"] / df["amount_ma20"]
    df["amount_ratio_5d_20d"] = df["amount_ma5"] / df["amount_ma20"]

    for window in (5, 10, 20, 60, 120, 250):
        df[f"distance_ma{window}_pct"] = (df["close"] / df[f"ma{window}"] - 1.0) * 100.0

    for window in (20, 60, 120):
        span = df[f"high_{window}d"] - df[f"low_{window}d"]
        df[f"range_{window}d_pct"] = (
            df[f"high_{window}d"] / df[f"low_{window}d"] - 1.0
        ) * 100.0
        df[f"position_{window}d_pct"] = np.where(
            span > 0,
            (df["close"] - df[f"low_{window}d"]) / span * 100.0,
            np.nan,
        )
        df[f"drawdown_from_{window}d_high_pct"] = (
            df["close"] / df[f"high_{window}d"] - 1.0
        ) * 100.0

    ma_short = df[["ma5", "ma10", "ma20"]]
    df["ma_5_10_20_convergence_pct"] = (
        (ma_short.max(axis=1) - ma_short.min(axis=1)) / df["close"] * 100.0
    ).where(ma_short.notna().all(axis=1))

    ma_medium = df[["ma5", "ma10", "ma20", "ma60"]]
    df["ma_5_10_20_60_dispersion_pct"] = (
        (ma_medium.max(axis=1) - ma_medium.min(axis=1)) / df["close"] * 100.0
    ).where(ma_medium.notna().all(axis=1))

    grouped = df.groupby("ts_code", sort=False, group_keys=False)
    df["ma20_slope_5d_pct"] = pct_change(grouped, "ma20", 5)
    df["ma60_slope_5d_pct"] = pct_change(grouped, "ma60", 5)
    df["ma120_slope_20d_pct"] = pct_change(grouped, "ma120", 20)
    df["ma250_slope_20d_pct"] = pct_change(grouped, "ma250", 20)

    valid20 = df["ma20"].notna()
    valid60 = df["ma60"].notna()
    valid120 = df["ma120"].notna()
    valid250 = df["ma250"].notna()
    df["close_above_ma20"] = valid_flag(df["close"] > df["ma20"], valid20)
    df["ma5_above_ma10_above_ma20"] = valid_flag(
        (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"]),
        valid20,
    )
    df["ma20_above_ma60"] = valid_flag(df["ma20"] > df["ma60"], valid60)
    df["close_above_ma120"] = valid_flag(df["close"] > df["ma120"], valid120)
    df["close_above_ma250"] = valid_flag(df["close"] > df["ma250"], valid250)
    df["ma60_above_ma120"] = valid_flag(df["ma60"] > df["ma120"], valid120)
    df["ma120_above_ma250"] = valid_flag(df["ma120"] > df["ma250"], valid250)

    grouped = df.groupby("ts_code", sort=False, group_keys=False)
    df["last_3d_low"] = rolling(grouped, "low", 3, "min")
    df["prior_20d_low_before_last_3d"] = grouped["low"].transform(
        lambda series: series.shift(3).rolling(20, min_periods=20).min()
    )
    valid_bottom = df[["last_3d_low", "prior_20d_low_before_last_3d"]].notna().all(axis=1)
    df["three_days_no_break_prior_20d_low"] = valid_flag(
        df["last_3d_low"] >= df["prior_20d_low_before_last_3d"],
        valid_bottom,
    )

    df["eligible_20d"] = (df["history_days"] >= 20).astype("int8")
    df["eligible_60d"] = (df["history_days"] >= 60).astype("int8")
    df["eligible_120d"] = (df["history_days"] >= 120).astype("int8")
    df["eligible_250d"] = (df["history_days"] >= 250).astype("int8")

    return df


def history_status(days: pd.Series) -> pd.Series:
    return pd.Series(
        np.select(
            [days >= 250, days >= 120, days >= 60, days >= 20],
            ["250日完整", "120日可用", "60日可用", "20日可用"],
            default="历史不足20日",
        ),
        index=days.index,
    )


def main() -> None:
    args = parse_args()
    history_path = Path(args.history)
    universe_path = Path(args.universe)
    output_path = Path(args.output)
    snapshot_dir = Path(args.snapshot_dir)

    if not history_path.exists():
        raise FileNotFoundError(f"找不到历史文件: {history_path}")
    if not universe_path.exists():
        raise FileNotFoundError(f"找不到基础股票池: {universe_path}")

    history = pd.read_csv(history_path, dtype={"ts_code": str, "trade_date": str})
    universe = pd.read_csv(universe_path, dtype={"ts_code": str})
    require_columns(history, HISTORY_REQUIRED, "历史文件")
    require_columns(universe, ["ts_code", "name", "segment"], "基础股票池")

    if history.duplicated(["ts_code", "trade_date"]).any():
        count = int(history.duplicated(["ts_code", "trade_date"]).sum())
        raise ValueError(f"历史文件存在重复股票-日期键: {count}")
    if universe["ts_code"].duplicated().any():
        count = int(universe["ts_code"].duplicated().sum())
        raise ValueError(f"基础股票池存在重复股票代码: {count}")

    numeric_columns = [column for column in HISTORY_REQUIRED if column not in {"ts_code", "trade_date"}]
    history[numeric_columns] = history[numeric_columns].apply(pd.to_numeric, errors="coerce")
    missing_raw = int(history[HISTORY_REQUIRED].isna().any(axis=1).sum())
    if missing_raw:
        raise ValueError(f"历史文件有必需字段缺失或非数字记录: {missing_raw}")

    calculated = calculate_indicators(history)
    latest_date = str(calculated["trade_date"].max())
    latest = calculated.loc[calculated["trade_date"] == latest_date].copy()

    universe_codes = set(universe["ts_code"])
    latest_codes = set(latest["ts_code"])
    missing_latest = sorted(universe_codes - latest_codes)
    unexpected_latest = sorted(latest_codes - universe_codes)
    if missing_latest or unexpected_latest:
        raise ValueError(
            "最新历史截面与基础股票池不一致: "
            f"missing_latest={len(missing_latest)}, unexpected_latest={len(unexpected_latest)}"
        )

    metadata_columns = [
        column
        for column in ["ts_code", "name", "segment", "turnover_rate", "total_mv_yi"]
        if column in universe.columns
    ]
    latest = universe[metadata_columns].merge(latest, on="ts_code", how="left", validate="one_to_one")
    latest["history_status"] = history_status(latest["history_days"])

    for periods in (5, 20, 60):
        return_column = f"return_{periods}d_pct"
        rank_column = f"relative_strength_{periods}d_percentile"
        excess_column = f"excess_return_{periods}d_vs_universe_median_pct"
        latest[rank_column] = latest[return_column].rank(pct=True, method="average") * 100.0
        latest[excess_column] = latest[return_column] - latest[return_column].median()

    drop_columns = ["true_range"]
    latest = latest.drop(columns=[column for column in drop_columns if column in latest.columns])

    preferred = [
        "ts_code",
        "name",
        "segment",
        "trade_date",
        "history_days",
        "history_status",
        "eligible_20d",
        "eligible_60d",
        "eligible_120d",
        "eligible_250d",
        "close",
        "pct_chg",
        "vol",
        "amount",
        "turnover_rate",
        "total_mv_yi",
    ]
    ordered = [column for column in preferred if column in latest.columns]
    ordered += [column for column in latest.columns if column not in ordered]
    latest = latest[ordered].sort_values("ts_code", kind="stable").reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"indicators_{latest_date}.csv"
    latest.to_csv(output_path, index=False, encoding="utf-8-sig")
    latest.to_csv(snapshot_path, index=False, encoding="utf-8-sig")

    history_days = latest["history_days"]
    summary = {
        "run_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trade_date": latest_date,
        "history_rows": int(len(history)),
        "history_dates": int(history["trade_date"].nunique()),
        "history_stocks": int(history["ts_code"].nunique()),
        "indicator_rows": int(len(latest)),
        "history_days_min": int(history_days.min()),
        "history_days_median": float(history_days.median()),
        "history_days_max": int(history_days.max()),
        "stocks_lt_20d": int((history_days < 20).sum()),
        "stocks_20_to_59d": int(((history_days >= 20) & (history_days < 60)).sum()),
        "stocks_60_to_119d": int(((history_days >= 60) & (history_days < 120)).sum()),
        "stocks_120_to_249d": int(((history_days >= 120) & (history_days < 250)).sum()),
        "stocks_250d": int((history_days >= 250).sum()),
        "missing_ma20": int(latest["ma20"].isna().sum()),
        "missing_ma60": int(latest["ma60"].isna().sum()),
        "missing_ma120": int(latest["ma120"].isna().sum()),
        "missing_ma250": int(latest["ma250"].isna().sum()),
        "output_file": str(output_path.resolve()),
        "snapshot_file": str(snapshot_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    preview_columns = [
        "ts_code",
        "name",
        "segment",
        "history_days",
        "close",
        "ma5",
        "ma20",
        "ma60",
        "ma120",
        "ma250",
        "return_20d_pct",
        "volume_ratio_1d_20d",
        "volatility_20d_pct",
        "position_60d_pct",
    ]
    preview_columns = [column for column in preview_columns if column in latest.columns]
    print("\n指标数据前5行：")
    print(latest[preview_columns].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
