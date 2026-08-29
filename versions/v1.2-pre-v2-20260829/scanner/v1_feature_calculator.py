#!/usr/bin/env python3
"""Calculate the V1-only stock feature layer without changing existing outputs."""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_HISTORY = "output/history_daily_latest.csv"
DEFAULT_INDICATORS = "output/indicators_latest.csv"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_OUTPUT = "output/v1_features_latest.csv"
DEFAULT_SNAPSHOT_DIR = "data/v1_features"

HISTORY_COLUMNS = ["ts_code", "trade_date", "open", "high", "low", "close", "vol"]
FEATURE_COLUMNS = [
    "ts_code",
    "trade_date",
    "vol_avg_prior_10d",
    "volume_ratio_1d_prior_10d",
    "close_median_20d",
    "center_band_ratio_20d_pct",
    "prior_20d_high",
    "breakout_vs_prior_20d_high_pct",
    "ma5_slope_3d_pct",
    "ma5_prev_slope_3d_pct",
    "ma5_slope_change_pct",
    "ma10_slope_3d_pct",
    "ma5_ma10_gap_pct",
    "upper_shadow_pct",
    "hist_5d_target_hit_rate_250d_pct",
    "hist_20d_target_hit_rate_250d_pct",
    "max_5d_return_250d_pct",
    "max_20d_return_250d_pct",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算V1策略专用特征")
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--indicators", default=DEFAULT_INDICATORS)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    return parser.parse_args()


def load_config(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    if config.get("model_version") != "v1.0.0":
        raise ValueError("strategy_v1.json model_version必须为v1.0.0")
    try:
        short_target = float(config["return_evaluation"]["short_target_pct"])
        medium_target = float(config["return_evaluation"]["medium_target_pct"])
        center_band = float(config["sideways_breakout"]["center_band_pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("strategy_v1.json缺少V1特征所需配置") from exc
    if short_target <= 0 or medium_target <= 0 or not 0 <= center_band < 100:
        raise ValueError("V1目标收益或中枢带宽配置无效")
    return config


def ratio_pct(numerator: float, denominator: float) -> float:
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator == 0:
        return np.nan
    return (numerator / denominator - 1.0) * 100.0


def calculate_latest_for_stock(
    group: pd.DataFrame,
    latest_date: int,
    center_band_pct: float,
    short_target_pct: float,
    medium_target_pct: float,
) -> dict[str, object] | None:
    if group.empty or int(group["trade_date"].iloc[-1]) != latest_date:
        return None

    ts_code = str(group["ts_code"].iloc[-1])
    close = group["close"].to_numpy(dtype="float64", copy=False)
    high = group["high"].to_numpy(dtype="float64", copy=False)
    low = group["low"].to_numpy(dtype="float64", copy=False)
    open_price = group["open"].to_numpy(dtype="float64", copy=False)
    vol = group["vol"].to_numpy(dtype="float64", copy=False)
    days = len(group)

    record: dict[str, object] = {column: np.nan for column in FEATURE_COLUMNS}
    record["ts_code"] = ts_code
    record["trade_date"] = str(latest_date)

    if days >= 11:
        prior_volumes = vol[-11:-1]
        if np.isfinite(prior_volumes).all():
            vol_average = float(prior_volumes.mean())
            record["vol_avg_prior_10d"] = vol_average
            if vol_average > 0 and math.isfinite(float(vol[-1])):
                record["volume_ratio_1d_prior_10d"] = float(vol[-1] / vol_average)

    if days >= 20:
        close_20d = close[-20:]
        if np.isfinite(close_20d).all():
            median_20d = float(np.median(close_20d))
            lower = median_20d * (1.0 - center_band_pct / 100.0)
            upper = median_20d * (1.0 + center_band_pct / 100.0)
            record["close_median_20d"] = median_20d
            record["center_band_ratio_20d_pct"] = float(
                ((close_20d >= lower) & (close_20d <= upper)).mean() * 100.0
            )

    if days >= 21:
        prior_highs = high[-21:-1]
        if np.isfinite(prior_highs).all():
            prior_high = float(prior_highs.max())
            record["prior_20d_high"] = prior_high
            record["breakout_vs_prior_20d_high_pct"] = ratio_pct(
                float(close[-1]), prior_high
            )

    ma5_today = float(close[-5:].mean()) if days >= 5 else np.nan
    ma10_today = float(close[-10:].mean()) if days >= 10 else np.nan

    if days >= 8:
        ma5_three_days_ago = float(close[-8:-3].mean())
        record["ma5_slope_3d_pct"] = ratio_pct(ma5_today, ma5_three_days_ago)
    if days >= 9:
        ma5_previous = float(close[-6:-1].mean())
        ma5_four_days_ago = float(close[-9:-4].mean())
        previous_slope = ratio_pct(ma5_previous, ma5_four_days_ago)
        record["ma5_prev_slope_3d_pct"] = previous_slope
        current_slope = record["ma5_slope_3d_pct"]
        if math.isfinite(float(current_slope)) and math.isfinite(previous_slope):
            record["ma5_slope_change_pct"] = float(current_slope) - previous_slope
    if days >= 13:
        ma10_three_days_ago = float(close[-13:-3].mean())
        record["ma10_slope_3d_pct"] = ratio_pct(ma10_today, ma10_three_days_ago)
    if days >= 10 and close[-1] != 0:
        record["ma5_ma10_gap_pct"] = float(
            abs(ma5_today - ma10_today) / close[-1] * 100.0
        )

    current_range = float(high[-1] - low[-1])
    if current_range == 0:
        record["upper_shadow_pct"] = 0.0
    elif current_range > 0:
        record["upper_shadow_pct"] = float(
            (high[-1] - max(open_price[-1], close[-1])) / current_range * 100.0
        )

    # Historical elasticity uses trailing returns wholly contained in the latest
    # 250 actual trading days. It never refers to a date after latest_date.
    if days >= 250:
        close_250d = close[-250:]
        if np.isfinite(close_250d).all() and (close_250d > 0).all():
            return_5d = (close_250d[5:] / close_250d[:-5] - 1.0) * 100.0
            return_20d = (close_250d[20:] / close_250d[:-20] - 1.0) * 100.0
            record["hist_5d_target_hit_rate_250d_pct"] = float(
                (return_5d >= short_target_pct).mean() * 100.0
            )
            record["hist_20d_target_hit_rate_250d_pct"] = float(
                (return_20d >= medium_target_pct).mean() * 100.0
            )
            record["max_5d_return_250d_pct"] = float(return_5d.max())
            record["max_20d_return_250d_pct"] = float(return_20d.max())

    return record


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8-sig", newline="") as handle:
            frame.to_csv(handle, index=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    history_path = Path(args.history)
    indicator_path = Path(args.indicators)
    config_path = Path(args.config)
    output_path = Path(args.output)

    for label, path in (
        ("历史日线", history_path),
        ("现有指标", indicator_path),
        ("V1配置", config_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label}文件不存在: {path}")

    config = load_config(config_path)
    return_config = config["return_evaluation"]
    breakout_config = config["sideways_breakout"]

    indicators = pd.read_csv(
        indicator_path,
        usecols=["ts_code", "trade_date"],
        dtype={"ts_code": "string", "trade_date": "string"},
    )
    if indicators.empty or indicators["trade_date"].nunique(dropna=False) != 1:
        raise ValueError("indicators_latest.csv必须包含且只包含一个交易日")
    if indicators["ts_code"].isna().any() or indicators["ts_code"].duplicated().any():
        raise ValueError("indicators_latest.csv存在空或重复ts_code")

    latest_date_text = str(indicators["trade_date"].iloc[0])
    if len(latest_date_text) != 8 or not latest_date_text.isdigit():
        raise ValueError(f"指标交易日格式无效: {latest_date_text}")
    latest_date = int(latest_date_text)
    pool_codes = set(indicators["ts_code"].astype(str))

    history = pd.read_csv(
        history_path,
        usecols=HISTORY_COLUMNS,
        dtype={
            "ts_code": "category",
            "trade_date": "int32",
            "open": "float64",
            "high": "float64",
            "low": "float64",
            "close": "float64",
            "vol": "float64",
        },
    )
    if history.empty:
        raise ValueError("历史日线为空")

    relevant = history["ts_code"].isin(pool_codes) & history["trade_date"].le(latest_date)
    if not bool(relevant.all()):
        history = history.loc[relevant].copy()
    if history.duplicated(["ts_code", "trade_date"]).any():
        duplicate_count = int(history.duplicated(["ts_code", "trade_date"]).sum())
        raise ValueError(f"历史日线存在重复ts_code-trade_date: {duplicate_count}")

    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    records: list[dict[str, object]] = []
    for _, group in history.groupby("ts_code", sort=False, observed=True):
        record = calculate_latest_for_stock(
            group=group,
            latest_date=latest_date,
            center_band_pct=float(breakout_config["center_band_pct"]),
            short_target_pct=float(return_config["short_target_pct"]),
            medium_target_pct=float(return_config["medium_target_pct"]),
        )
        if record is not None:
            records.append(record)

    calculated = pd.DataFrame.from_records(records, columns=FEATURE_COLUMNS)
    output = indicators.merge(
        calculated.drop(columns=["trade_date"]),
        on="ts_code",
        how="left",
        validate="one_to_one",
    )
    output = output[FEATURE_COLUMNS].sort_values("ts_code", kind="stable").reset_index(drop=True)

    if len(output) != len(indicators):
        raise RuntimeError("V1输出行数与当前指标股票池不一致")
    if output["ts_code"].duplicated().any():
        raise RuntimeError("V1输出存在重复ts_code")
    if output["trade_date"].nunique(dropna=False) != 1:
        raise RuntimeError("V1输出交易日不唯一")

    snapshot_path = Path(args.snapshot_dir) / f"v1_features_{latest_date_text}.csv"
    atomic_write_csv(output, snapshot_path)
    atomic_write_csv(output, output_path)

    missing = {
        column: int(output[column].isna().sum())
        for column in FEATURE_COLUMNS
        if column not in {"ts_code", "trade_date"}
    }
    summary = {
        "model_version": config["model_version"],
        "trade_date": latest_date_text,
        "history_source": str(history_path),
        "history_rows_read": int(len(history)),
        "indicator_rows": int(len(indicators)),
        "calculated_current_rows": int(len(calculated)),
        "output_rows": int(len(output)),
        "output_columns": int(len(output.columns)),
        "duplicate_ts_code": int(output["ts_code"].duplicated().sum()),
        "missing": missing,
        "output_file": str(output_path.resolve()),
        "snapshot_file": str(snapshot_path.resolve()),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
