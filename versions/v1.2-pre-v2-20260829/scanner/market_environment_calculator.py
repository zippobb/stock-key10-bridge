#!/usr/bin/env python3
"""Calculate a descriptive V1 market environment from latest stock indicators."""

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


DEFAULT_INDICATORS = "output/indicators_latest.csv"
DEFAULT_HISTORY = "output/history_daily_latest.csv"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_OUTPUT = "output/market_environment_latest.json"
DEFAULT_SNAPSHOT_DIR = "data/market"

HISTORY_COLUMNS = ["ts_code", "trade_date", "low", "close"]
MARKET_BOTTOM_WARNING = (
    "市场底部判断仅表示下跌压力可能减弱，不构成绝对买入开关，"
    "仍允许强势板块和个股独立运行。"
)

FIELD_MAPPING = {
    "是否站上MA20": "close_above_ma20",
    "MA5、MA10、MA20多头排列": "ma5_above_ma10_above_ma20",
    "5日收益率": "return_5d_pct",
    "20日收益率": "return_20d_pct",
    "MA20斜率": "ma20_slope_5d_pct",
    "连续3日未创新低": "three_days_no_break_prior_20d_low",
}

POLICIES = {
    "上涨": "可正常放宽候选数量，重点关注强势板块的右侧初期和第二浪机会。",
    "横盘震荡": "正常筛选，优先横盘突破、底部转右侧和缩量不跌信号。",
    "下跌中反弹": "优先保留强势板块的领先个股和底部右侧信号，其他候选降级。",
    "下跌": "不关闭选股，只保留独立走强的板块和个股，并明确提示市场风险。",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算V1全市场环境")
    parser.add_argument("--indicators", default=DEFAULT_INDICATORS)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    return parser.parse_args()


def load_market_config(path: Path) -> tuple[dict[str, object], dict[str, float], dict[str, float]]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    excluded_from_stock_scoring = config.get("exclude_from_stock_scoring", [])
    if "three_days_no_break_prior_20d_low" not in excluded_from_stock_scoring:
        raise ValueError(
            "strategy_v1.json必须将three_days_no_break_prior_20d_low排除出个股评分"
        )
    try:
        market = config["market"]
        weights = {key: float(value) for key, value in market["score_weights"].items()}
        thresholds = {key: float(value) for key, value in market["thresholds"].items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("strategy_v1.json市场配置缺失或格式错误") from exc

    required_weights = {
        "above_ma20_ratio",
        "bullish_alignment_ratio",
        "positive_5d_ratio",
        "positive_20d_ratio",
        "median_return_5d",
        "median_return_20d",
        "median_ma20_slope",
    }
    required_thresholds = {
        "up_score_min",
        "up_above_ma20_min_pct",
        "down_score_max",
        "down_above_ma20_max_pct",
        "rebound_positive_5d_min_pct",
    }
    if set(weights) != required_weights:
        raise ValueError("市场评分权重字段不完整或包含未知字段")
    if set(thresholds) != required_thresholds:
        raise ValueError("市场分类阈值字段不完整或包含未知字段")
    if not math.isclose(sum(weights.values()), 100.0, abs_tol=1e-9):
        raise ValueError(f"市场评分权重总和必须为100，当前为{sum(weights.values())}")
    if market.get("hard_gate") is not False:
        raise ValueError("market.hard_gate必须严格为false")
    return market, weights, thresholds


def binary_ratio(series: pd.Series, label: str) -> tuple[float, int]:
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.notna()
    observed = numeric.loc[valid]
    invalid = ~observed.isin([0, 1])
    if invalid.any():
        raise ValueError(f"{label}包含非0/1有效值: {int(invalid.sum())}")
    if observed.empty:
        raise ValueError(f"{label}没有有效样本")
    return float(observed.mean() * 100.0), int(len(observed))


def median_value(series: pd.Series, label: str) -> tuple[float, int]:
    numeric = pd.to_numeric(series, errors="coerce")
    valid = numeric.dropna()
    if valid.empty:
        raise ValueError(f"{label}没有有效样本")
    return float(valid.median()), int(len(valid))


def clipped_score(value: float, multiplier: float) -> float:
    return float(np.clip(50.0 + value * multiplier, 0.0, 100.0))


def classify_market(
    market_score: float,
    metrics: dict[str, float],
    thresholds: dict[str, float],
) -> str:
    if (
        market_score >= thresholds["up_score_min"]
        and metrics["above_ma20_ratio_pct"] >= thresholds["up_above_ma20_min_pct"]
        and metrics["median_return_20d_pct"] > 0
    ):
        return "上涨"
    if (
        metrics["median_return_20d_pct"] < 0
        and metrics["median_return_5d_pct"] > 0
        and metrics["positive_5d_ratio_pct"]
        >= thresholds["rebound_positive_5d_min_pct"]
    ):
        return "下跌中反弹"
    if (
        market_score < thresholds["down_score_max"]
        and metrics["above_ma20_ratio_pct"] < thresholds["down_above_ma20_max_pct"]
        and metrics["median_return_20d_pct"] < 0
        and metrics["median_return_5d_pct"] < 0
    ):
        return "下跌"
    return "横盘震荡"


def calculate_market_bottom(
    history_path: Path,
    pool_codes: set[str],
    latest_date: int,
) -> tuple[dict[str, float], dict[str, int], list[dict[str, object]], dict[str, object]]:
    history = pd.read_csv(
        history_path,
        usecols=HISTORY_COLUMNS,
        dtype={
            "ts_code": "category",
            "trade_date": "int32",
            "low": "float64",
            "close": "float64",
        },
    )
    if history.empty:
        raise ValueError("历史日线为空，无法计算市场底部")

    future_rows = int(history["trade_date"].gt(latest_date).sum())
    relevant = history["ts_code"].isin(pool_codes) & history["trade_date"].le(latest_date)
    if not bool(relevant.all()):
        history = history.loc[relevant].copy()
    if history.duplicated(["ts_code", "trade_date"]).any():
        count = int(history.duplicated(["ts_code", "trade_date"]).sum())
        raise ValueError(f"历史日线存在重复股票-日期键: {count}")

    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    history.reset_index(drop=True, inplace=True)
    market_dates = sorted(int(value) for value in history["trade_date"].unique())
    if len(market_dates) < 60 or market_dates[-1] != latest_date:
        raise ValueError(
            f"市场底部分析至少需要60个市场交易日并覆盖最新日，"
            f"当前days={len(market_dates)}, last={market_dates[-1]}"
        )

    grouped = history.groupby("ts_code", sort=False, observed=True, group_keys=False)
    prior_20d_low = grouped["low"].transform(
        lambda series: series.shift(1).rolling(20, min_periods=20).min()
    )
    valid_new_low = history["low"].notna() & prior_20d_low.notna()
    history["is_new_20d_low"] = np.nan
    history.loc[valid_new_low, "is_new_20d_low"] = (
        history.loc[valid_new_low, "low"] < prior_20d_low.loc[valid_new_low]
    ).astype("float64")

    grouped = history.groupby("ts_code", sort=False, observed=True, group_keys=False)
    three_day_new_low_count = (
        grouped["is_new_20d_low"]
        .rolling(3, min_periods=3)
        .sum()
        .reset_index(level=0, drop=True)
        .sort_index()
    )
    history["no_new_low_3d"] = np.where(
        three_day_new_low_count.notna(),
        three_day_new_low_count.eq(0).astype("float64"),
        np.nan,
    )
    history["return_3d_pct"] = grouped["close"].pct_change(
        periods=3, fill_method=None
    ) * 100.0

    analysis_dates = market_dates[-60:]
    recent = history.loc[history["trade_date"].isin(analysis_dates)]
    daily = recent.groupby("trade_date", sort=True, observed=True).agg(
        new_20d_low_ratio_pct=("is_new_20d_low", lambda series: series.mean() * 100.0),
        new_20d_low_valid_samples=("is_new_20d_low", "count"),
        three_day_no_new_low_ratio_pct=(
            "no_new_low_3d",
            lambda series: series.mean() * 100.0,
        ),
        three_day_no_new_low_valid_samples=("no_new_low_3d", "count"),
    )
    if len(daily) < 60 or latest_date not in daily.index:
        raise ValueError("最近60个市场交易日汇总不完整")

    latest_daily = daily.loc[latest_date]
    recent_10d = daily.tail(10)
    if len(recent_10d) != 10:
        raise ValueError("最近10个市场交易日汇总不完整")
    peak_10d = float(recent_10d["new_20d_low_ratio_pct"].max())
    current_new_low = float(latest_daily["new_20d_low_ratio_pct"])
    contraction = (
        (peak_10d - current_new_low) / peak_10d * 100.0
        if math.isfinite(peak_10d) and peak_10d > 0
        else np.nan
    )
    no_new_low_10d_low = float(
        recent_10d["three_day_no_new_low_ratio_pct"].min()
    )
    current_no_new_low = float(latest_daily["three_day_no_new_low_ratio_pct"])

    latest_stocks = history.loc[history["trade_date"].eq(latest_date)]
    return_3d = latest_stocks["return_3d_pct"].dropna()
    if return_3d.empty:
        raise ValueError("最新交易日没有有效3日收益样本")
    positive_3d_ratio = float(return_3d.gt(0).mean() * 100.0)
    median_return_3d = float(return_3d.median())

    bottom_metrics = {
        "new_20d_low_ratio_pct": current_new_low,
        "three_day_no_new_low_ratio_pct": current_no_new_low,
        "new_low_ratio_peak_10d_pct": peak_10d,
        "new_low_ratio_contraction_from_10d_peak_pct": float(contraction),
        "three_day_no_new_low_ratio_10d_low_pct": no_new_low_10d_low,
        "three_day_no_new_low_improvement_pct_point": (
            current_no_new_low - no_new_low_10d_low
        ),
        "positive_3d_ratio_pct": positive_3d_ratio,
        "median_return_3d_pct": median_return_3d,
    }
    bottom_valid_counts = {
        "new_20d_low_ratio_pct": int(latest_daily["new_20d_low_valid_samples"]),
        "three_day_no_new_low_ratio_pct": int(
            latest_daily["three_day_no_new_low_valid_samples"]
        ),
        "new_low_ratio_peak_10d_pct": int(
            recent_10d["new_20d_low_ratio_pct"].notna().sum()
        ),
        "new_low_ratio_contraction_from_10d_peak_pct": int(
            recent_10d["new_20d_low_ratio_pct"].notna().sum()
        ),
        "three_day_no_new_low_ratio_10d_low_pct": int(
            recent_10d["three_day_no_new_low_ratio_pct"].notna().sum()
        ),
        "three_day_no_new_low_improvement_pct_point": int(
            recent_10d["three_day_no_new_low_ratio_pct"].notna().sum()
        ),
        "positive_3d_ratio_pct": int(len(return_3d)),
        "median_return_3d_pct": int(len(return_3d)),
    }
    recent_10d_details = [
        {
            "trade_date": str(int(trade_date)),
            "new_20d_low_ratio_pct": round(float(row["new_20d_low_ratio_pct"]), 4),
            "new_20d_low_valid_samples": int(row["new_20d_low_valid_samples"]),
            "three_day_no_new_low_ratio_pct": round(
                float(row["three_day_no_new_low_ratio_pct"]), 4
            ),
            "three_day_no_new_low_valid_samples": int(
                row["three_day_no_new_low_valid_samples"]
            ),
        }
        for trade_date, row in recent_10d.iterrows()
    ]
    future_guard = {
        "history_rows_used": int(len(history)),
        "history_market_days_available": int(len(market_dates)),
        "analysis_market_days": 60,
        "history_cutoff_trade_date": str(latest_date),
        "future_rows_in_source": future_rows,
        "future_rows_used": 0,
        "is_new_20d_low_prior_window_days": 20,
        "current_day_excluded_from_prior_20d_low": True,
        "per_stock_actual_trading_days": True,
    }
    return bottom_metrics, bottom_valid_counts, recent_10d_details, future_guard


def evaluate_market_bottom(
    market_state: str,
    bottom_metrics: dict[str, float],
) -> tuple[str, int, list[str]]:
    condition_a = (
        bottom_metrics["new_low_ratio_peak_10d_pct"] >= 5.0
        and bottom_metrics["new_low_ratio_contraction_from_10d_peak_pct"] >= 50.0
    )
    condition_b = (
        bottom_metrics["three_day_no_new_low_ratio_pct"] >= 95.0
        and bottom_metrics["three_day_no_new_low_improvement_pct_point"] >= 3.0
    )
    condition_c = (
        bottom_metrics["positive_3d_ratio_pct"] >= 50.0
        and bottom_metrics["median_return_3d_pct"] > 0.0
    )
    signal_count = int(condition_a) + int(condition_b) + int(condition_c)
    evidence = [
        (
            f"A 新低股明显收缩：{'满足' if condition_a else '未满足'}；"
            f"10日峰值{bottom_metrics['new_low_ratio_peak_10d_pct']:.2f}%，"
            f"较峰值收缩{bottom_metrics['new_low_ratio_contraction_from_10d_peak_pct']:.2f}%。"
        ),
        (
            f"B 三日未创新低广度改善：{'满足' if condition_b else '未满足'}；"
            f"当前比例{bottom_metrics['three_day_no_new_low_ratio_pct']:.2f}%，"
            f"较10日低点改善{bottom_metrics['three_day_no_new_low_improvement_pct_point']:.2f}个百分点。"
        ),
        (
            f"C 短期市场修复：{'满足' if condition_c else '未满足'}；"
            f"3日正收益比例{bottom_metrics['positive_3d_ratio_pct']:.2f}%，"
            f"3日收益中位数{bottom_metrics['median_return_3d_pct']:.2f}%。"
        ),
    ]
    if condition_a and condition_b and condition_c:
        status = "底部修复已获右侧确认"
    elif market_state in {"上涨", "横盘震荡"}:
        status = "非底部判断阶段"
    elif condition_a and condition_b:
        status = "疑似止跌"
    elif condition_a or condition_b:
        status = "底部迹象不足"
    else:
        status = "未见底"
    return status, signal_count, evidence


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    indicator_path = Path(args.indicators)
    history_path = Path(args.history)
    config_path = Path(args.config)
    output_path = Path(args.output)

    if not indicator_path.is_file():
        raise FileNotFoundError(f"指标文件不存在: {indicator_path}")
    if not history_path.is_file():
        raise FileNotFoundError(f"历史文件不存在: {history_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"策略配置不存在: {config_path}")

    market_config, weights, thresholds = load_market_config(config_path)
    required_columns = ["ts_code", "trade_date", *FIELD_MAPPING.values()]
    actual_columns = pd.read_csv(indicator_path, nrows=0).columns.tolist()
    missing_columns = [column for column in required_columns if column not in actual_columns]
    if missing_columns:
        raise ValueError(f"indicators_latest.csv缺少真实字段: {missing_columns}")

    indicators = pd.read_csv(
        indicator_path,
        usecols=required_columns,
        dtype={"ts_code": "string", "trade_date": "string"},
    )
    if indicators.empty:
        raise ValueError("indicators_latest.csv为空")
    dates = indicators["trade_date"].dropna()
    if dates.empty:
        raise ValueError("indicators_latest.csv没有有效交易日")
    latest_date = str(dates.max())
    latest = indicators.loc[indicators["trade_date"].eq(latest_date)].copy()
    if latest["ts_code"].isna().any() or latest["ts_code"].duplicated().any():
        raise ValueError("最新交易日存在空或重复ts_code")
    pool_codes = set(latest["ts_code"].astype(str))

    metrics: dict[str, float] = {}
    valid_counts: dict[str, int] = {}
    metrics["above_ma20_ratio_pct"], valid_counts["above_ma20_ratio_pct"] = binary_ratio(
        latest[FIELD_MAPPING["是否站上MA20"]], "是否站上MA20"
    )
    (
        metrics["bullish_alignment_ratio_pct"],
        valid_counts["bullish_alignment_ratio_pct"],
    ) = binary_ratio(
        latest[FIELD_MAPPING["MA5、MA10、MA20多头排列"]],
        "MA5、MA10、MA20多头排列",
    )

    return_5d = pd.to_numeric(latest[FIELD_MAPPING["5日收益率"]], errors="coerce")
    return_20d = pd.to_numeric(latest[FIELD_MAPPING["20日收益率"]], errors="coerce")
    metrics["positive_5d_ratio_pct"], valid_counts["positive_5d_ratio_pct"] = binary_ratio(
        return_5d.gt(0).where(return_5d.notna()), "5日正收益"
    )
    metrics["positive_20d_ratio_pct"], valid_counts["positive_20d_ratio_pct"] = binary_ratio(
        return_20d.gt(0).where(return_20d.notna()), "20日正收益"
    )
    (
        metrics["no_new_low_3d_ratio_pct"],
        valid_counts["no_new_low_3d_ratio_pct"],
    ) = binary_ratio(
        latest[FIELD_MAPPING["连续3日未创新低"]], "连续3日未创新低"
    )
    metrics["median_return_5d_pct"], valid_counts["median_return_5d_pct"] = median_value(
        return_5d, "5日收益率"
    )
    metrics["median_return_20d_pct"], valid_counts["median_return_20d_pct"] = median_value(
        return_20d, "20日收益率"
    )
    (
        metrics["median_ma20_slope_pct"],
        valid_counts["median_ma20_slope_pct"],
    ) = median_value(latest[FIELD_MAPPING["MA20斜率"]], "MA20斜率")

    component_scores = {
        "above_ma20_ratio": metrics["above_ma20_ratio_pct"],
        "bullish_alignment_ratio": metrics["bullish_alignment_ratio_pct"],
        "positive_5d_ratio": metrics["positive_5d_ratio_pct"],
        "positive_20d_ratio": metrics["positive_20d_ratio_pct"],
        "median_return_5d": clipped_score(metrics["median_return_5d_pct"], 5.0),
        "median_return_20d": clipped_score(metrics["median_return_20d_pct"], 2.5),
        "median_ma20_slope": clipped_score(metrics["median_ma20_slope_pct"], 10.0),
    }
    weighted_components = {
        key: {
            "score": round(component_scores[key], 4),
            "weight": weights[key],
            "weighted_score": round(component_scores[key] * weights[key] / 100.0, 4),
        }
        for key in weights
    }
    market_score = round(
        sum(component_scores[key] * weights[key] / 100.0 for key in weights), 2
    )
    market_state = classify_market(market_score, metrics, thresholds)
    (
        bottom_metrics,
        bottom_valid_counts,
        recent_10d_details,
        future_data_guard,
    ) = calculate_market_bottom(
        history_path=history_path,
        pool_codes=pool_codes,
        latest_date=int(latest_date),
    )
    (
        market_bottom_status,
        market_bottom_signal_count,
        market_bottom_evidence,
    ) = evaluate_market_bottom(market_state, bottom_metrics)
    rounded_metrics = {key: round(value, 4) for key, value in metrics.items()}
    rounded_bottom_metrics = {
        key: (round(value, 4) if math.isfinite(value) else None)
        for key, value in bottom_metrics.items()
    }
    market_summary = (
        f"当前市场处于{market_state}，市场得分{market_score:.2f}，"
        f"站上MA20比例为{rounded_metrics['above_ma20_ratio_pct']:.2f}%。"
        f"5日和20日收益中位数分别为{rounded_metrics['median_return_5d_pct']:.2f}%和"
        f"{rounded_metrics['median_return_20d_pct']:.2f}%，用于描述当前市场趋势。"
    )

    result: dict[str, object] = {
        "trade_date": latest_date,
        "universe_size": int(len(latest)),
        **rounded_metrics,
        **rounded_bottom_metrics,
        "market_score": market_score,
        "market_state": market_state,
        "hard_gate": bool(market_config["hard_gate"]),
        "candidate_policy": POLICIES[market_state],
        "market_summary": market_summary,
        "market_bottom_status": market_bottom_status,
        "market_bottom_signal_count": market_bottom_signal_count,
        "market_bottom_evidence": market_bottom_evidence,
        "market_bottom_warning": MARKET_BOTTOM_WARNING,
        "valid_sample_counts": valid_counts,
        "market_bottom_valid_sample_counts": bottom_valid_counts,
        "market_bottom_recent_10d": recent_10d_details,
        "future_data_guard": future_data_guard,
        "field_mapping": FIELD_MAPPING,
        "score_weight_sum": sum(weights.values()),
        "score_components": weighted_components,
    }
    serialized = json.dumps(
        result, ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"
    snapshot_path = Path(args.snapshot_dir) / f"market_environment_{latest_date}.json"
    atomic_write_text(snapshot_path, serialized)
    atomic_write_text(output_path, serialized)

    print(serialized, end="")
    print(
        json.dumps(
            {
                "output_file": str(output_path.resolve()),
                "snapshot_file": str(snapshot_path.resolve()),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
