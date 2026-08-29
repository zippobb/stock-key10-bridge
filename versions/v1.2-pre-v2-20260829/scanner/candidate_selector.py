#!/usr/bin/env python3
"""Generate unvetoed V1 raw technical candidates from latest snapshot inputs."""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_BASE = "output/base_universe_latest.csv"
DEFAULT_INDICATORS = "output/indicators_latest.csv"
DEFAULT_SECTORS = "output/sector_indicators_latest.csv"
DEFAULT_V1 = "output/v1_features_latest.csv"
DEFAULT_MARKET = "output/market_environment_latest.json"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_OUTPUT = "output/raw_candidates_latest.csv"
DEFAULT_SNAPSHOT_DIR = "data/candidates"
DEFAULT_DIAGNOSTICS = "output/candidate_diagnostics_latest.json"

EXCLUDED_INDUSTRIES = {"白酒", "银行", "证券"}

FIELD_MAPPING = {
    "股票键": "ts_code",
    "股票名称": "name",
    "板块/市场板": "segment",
    "行业": "industry",
    "收盘价": "close",
    "当日涨跌幅": "pct_chg",
    "20日振幅": "range_20d_pct",
    "20日中枢占比": "center_band_ratio_20d_pct",
    "MA20五日斜率": "ma20_slope_5d_pct",
    "当日量/前10日均量": "volume_ratio_1d_prior_10d",
    "相对前20日高点突破": "breakout_vs_prior_20d_high_pct",
    "MA5三日斜率": "ma5_slope_3d_pct",
    "MA5/MA10间距": "ma5_ma10_gap_pct",
    "5日收益": "return_5d_pct",
    "20日收益": "return_20d_pct",
    "60日收益": "return_60d_pct",
    "60日价格位置": "position_60d_pct",
    "距离MA10": "distance_ma10_pct",
    "距离MA20": "distance_ma20_pct",
    "距20日高点回撤": "drawdown_from_20d_high_pct",
    "5日均量/20日均量": "volume_ratio_5d_20d",
    "ATR14百分比": "atr14_pct",
    "历史5日目标命中率": "hist_5d_target_hit_rate_250d_pct",
    "历史20日目标命中率": "hist_20d_target_hit_rate_250d_pct",
    "板块资格": "selection_eligible",
    "板块原始评分": "sector_strength_score",
    "板块排名": "selection_rank",
}

BASE_COLUMNS = ["ts_code", "trade_date", "name", "industry", "segment"]
INDICATOR_COLUMNS = [
    "ts_code",
    "trade_date",
    "close",
    "pct_chg",
    "range_20d_pct",
    "ma20_slope_5d_pct",
    "position_60d_pct",
    "return_5d_pct",
    "return_20d_pct",
    "return_60d_pct",
    "distance_ma10_pct",
    "distance_ma20_pct",
    "drawdown_from_20d_high_pct",
    "volume_ratio_5d_20d",
    "atr14_pct",
]
V1_COLUMNS = [
    "ts_code",
    "trade_date",
    "center_band_ratio_20d_pct",
    "volume_ratio_1d_prior_10d",
    "breakout_vs_prior_20d_high_pct",
    "ma5_slope_3d_pct",
    "ma5_ma10_gap_pct",
    "hist_5d_target_hit_rate_250d_pct",
    "hist_20d_target_hit_rate_250d_pct",
]
SECTOR_COLUMNS = [
    "industry",
    "trade_date",
    "selection_eligible",
    "selection_rank",
    "sector_strength_score",
    "excluded_by_rule",
]

STOCK_FORMULA_FIELDS = {
    "range_20d_pct",
    "center_band_ratio_20d_pct",
    "ma20_slope_5d_pct",
    "volume_ratio_1d_prior_10d",
    "breakout_vs_prior_20d_high_pct",
    "pct_chg",
    "return_5d_pct",
    "return_20d_pct",
    "return_60d_pct",
    "ma5_slope_3d_pct",
    "position_60d_pct",
    "distance_ma10_pct",
    "ma5_ma10_gap_pct",
    "distance_ma20_pct",
    "drawdown_from_20d_high_pct",
    "volume_ratio_5d_20d",
    "atr14_pct",
    "hist_5d_target_hit_rate_250d_pct",
    "hist_20d_target_hit_rate_250d_pct",
}

OUTPUT_COLUMNS = [
    "trade_date",
    "ts_code",
    "name",
    "board",
    "industry",
    "close",
    "pct_chg",
    "market_state",
    "market_score",
    "market_bottom_status",
    "sector_score_original",
    "sector_rank",
    "sector_rank_percentile",
    "matched_signals",
    "primary_signal",
    "stage_label",
    "stage_confidence",
    "pattern_score",
    "volume_price_score",
    "stage_score",
    "sector_score",
    "relative_strength_elasticity_score",
    "total_score",
    "candidate_grade",
    "volume_ratio_1d_prior_10d",
    "return_5d_pct",
    "return_20d_pct",
    "atr14_pct",
    "return_5d_market_percentile",
    "return_20d_market_percentile",
    "return_5d_industry_percentile",
    "hist_5d_target_hit_rate_250d_pct",
    "hist_20d_target_hit_rate_250d_pct",
    "hist_5d_target_hit_rate_percentile",
    "hist_20d_target_hit_rate_percentile",
    "atr14_pct_percentile",
    "technical_reason",
    "manual_review_note",
    "risk_review_pending",
]

SIGNAL_LABELS = {
    "horizontal_breakout_strong": "横盘强放量突破",
    "horizontal_breakout_watch": "横盘放量突破观察",
    "bottom_right_side": "底部放量转右侧",
    "bottom_right_side_leader_relaxed": "强势板块领先股宽松右侧",
    "right_side_contraction_strong": "右侧初期强缩量不跌",
    "right_side_contraction_watch": "右侧初期缩量不跌观察",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成V1原始技术候选")
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--indicators", default=DEFAULT_INDICATORS)
    parser.add_argument("--sectors", default=DEFAULT_SECTORS)
    parser.add_argument("--v1", default=DEFAULT_V1)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--diagnostics", default=DEFAULT_DIAGNOSTICS)
    return parser.parse_args()


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少真实字段: {missing}")


def ensure_unique(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    duplicate_count = int(frame.duplicated(columns).sum())
    if duplicate_count:
        raise ValueError(f"{label}存在重复键{columns}: {duplicate_count}")


def percentile(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, method="average") * 100.0


def score_distribution(frame: pd.DataFrame, column: str) -> dict[str, float | None]:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(float(values.min()), 2),
        "median": round(float(values.median()), 2),
        "max": round(float(values.max()), 2),
    }


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_inputs(args: argparse.Namespace) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    dict[str, object],
    dict[str, object],
]:
    paths = {
        "基础股票池": Path(args.base),
        "个股指标": Path(args.indicators),
        "板块指标": Path(args.sectors),
        "V1特征": Path(args.v1),
        "市场环境": Path(args.market),
        "策略配置": Path(args.config),
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label}不存在: {path}")

    base = pd.read_csv(paths["基础股票池"], dtype={"ts_code": "string", "trade_date": "string"})
    indicators = pd.read_csv(paths["个股指标"], dtype={"ts_code": "string", "trade_date": "string"})
    sectors = pd.read_csv(paths["板块指标"], dtype={"trade_date": "string"})
    v1 = pd.read_csv(paths["V1特征"], dtype={"ts_code": "string", "trade_date": "string"})
    market = json.loads(paths["市场环境"].read_text(encoding="utf-8"))
    config = json.loads(paths["策略配置"].read_text(encoding="utf-8"))

    require_columns(base, BASE_COLUMNS, "基础股票池")
    require_columns(indicators, INDICATOR_COLUMNS, "个股指标")
    require_columns(sectors, SECTOR_COLUMNS, "板块指标")
    require_columns(v1, V1_COLUMNS, "V1特征")
    ensure_unique(base, ["ts_code"], "基础股票池")
    ensure_unique(indicators, ["ts_code"], "个股指标")
    ensure_unique(v1, ["ts_code"], "V1特征")
    ensure_unique(sectors, ["industry"], "板块指标")

    dates = {
        "base": set(base["trade_date"].dropna().astype(str)),
        "indicators": set(indicators["trade_date"].dropna().astype(str)),
        "sectors": set(sectors["trade_date"].dropna().astype(str)),
        "v1": set(v1["trade_date"].dropna().astype(str)),
        "market": {str(market.get("trade_date"))},
    }
    all_dates = set().union(*dates.values())
    if any(len(values) != 1 for values in dates.values()) or len(all_dates) != 1:
        raise ValueError(f"输入交易日不一致: {dates}")
    if market.get("hard_gate") is not False:
        raise ValueError("市场hard_gate必须为false，市场状态不得关闭选股")

    excluded_fields = config.get("exclude_from_stock_scoring")
    if not isinstance(excluded_fields, list) or not excluded_fields:
        raise ValueError("strategy_v1.json缺少exclude_from_stock_scoring")
    forbidden_intersection = sorted(set(excluded_fields) & STOCK_FORMULA_FIELDS)
    if forbidden_intersection:
        raise ValueError(f"排除字段进入个股公式: {forbidden_intersection}")

    expected_score_weights = {
        "pattern": 30,
        "volume_price": 20,
        "stage": 20,
        "sector_sync": 15,
        "relative_strength_elasticity": 15,
    }
    score_config = config.get("score_weights", {})
    for key, value in expected_score_weights.items():
        if float(score_config.get(key, -1)) != value:
            raise ValueError(f"评分权重配置不符合V1: {key}")
    if sum(expected_score_weights.values()) != 100:
        raise ValueError("V1评分总权重必须为100")
    return base, indicators, sectors, v1, market, config


def select_sectors(
    sectors: pd.DataFrame,
    top_fraction: float,
) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    sector = sectors.copy()
    for column in ["selection_eligible", "selection_rank", "sector_strength_score", "excluded_by_rule"]:
        sector[column] = pd.to_numeric(sector[column], errors="coerce")
    eligible = sector.loc[
        sector["selection_eligible"].eq(1)
        & sector["excluded_by_rule"].eq(0)
        & ~sector["industry"].isin(EXCLUDED_INDUSTRIES)
        & sector["sector_strength_score"].notna()
    ].copy()
    if eligible.empty:
        raise ValueError("没有符合现有资格规则的板块")
    if not 0 < top_fraction <= 1:
        raise ValueError("sector_top_fraction必须在(0,1]范围内")

    eligible["sector_rank_percentile"] = percentile(eligible["sector_strength_score"])
    eligible.sort_values(
        ["sector_strength_score", "industry"],
        ascending=[False, True],
        kind="stable",
        inplace=True,
    )
    keep_count = max(1, math.ceil(len(eligible) * top_fraction))
    cutoff_score = float(eligible.iloc[keep_count - 1]["sector_strength_score"])
    selected = eligible.loc[eligible["sector_strength_score"].ge(cutoff_score)].copy()
    return eligible, selected, cutoff_score


def signal_and_scores(row: pd.Series) -> pd.Series:
    signals: list[str] = []
    signal_pattern_scores: dict[str, float] = {}

    horizontal_base = (
        row["range_20d_pct"] <= 18
        and row["center_band_ratio_20d_pct"] >= 70
        and abs(row["ma20_slope_5d_pct"]) <= 3
        and row["volume_ratio_1d_prior_10d"] >= 1.5
        and row["breakout_vs_prior_20d_high_pct"] >= -2
        and row["pct_chg"] >= 0
    )
    if horizontal_base:
        strong = (
            row["volume_ratio_1d_prior_10d"] >= 2
            and row["breakout_vs_prior_20d_high_pct"] >= 0
        )
        signal = "horizontal_breakout_strong" if strong else "horizontal_breakout_watch"
        extras = (
            2 * int(row["volume_ratio_1d_prior_10d"] >= 2)
            + 2 * int(row["breakout_vs_prior_20d_high_pct"] >= 0)
            + int(row["range_20d_pct"] <= 12)
            + int(row["center_band_ratio_20d_pct"] >= 80)
        )
        signals.append(signal)
        signal_pattern_scores[signal] = min(30.0, (26.0 if strong else 22.0) + extras)

    bottom_common = (
        row["volume_ratio_1d_prior_10d"] >= 1.5
        and row["return_5d_pct"] > 0
        and row["ma5_slope_3d_pct"] > 0
    )
    bottom_normal = (
        bottom_common
        and row["position_60d_pct"] <= 60
        and row["return_5d_industry_percentile"] >= 70
        and row["distance_ma10_pct"] >= -2
    )
    bottom_relaxed = (
        bottom_common
        and row["sector_rank_percentile"] >= 75
        and row["return_5d_industry_percentile"] >= 80
        and row["position_60d_pct"] <= 75
    )
    bottom_signal = ""
    if bottom_normal:
        bottom_signal = "bottom_right_side"
    elif bottom_relaxed:
        bottom_signal = "bottom_right_side_leader_relaxed"
    if bottom_signal:
        extras = (
            2 * int(row["sector_rank_percentile"] >= 90)
            + 2 * int(row["return_5d_industry_percentile"] >= 90)
            + int(row["position_60d_pct"] <= 40)
            + int(row["volume_ratio_1d_prior_10d"] >= 2)
            + int(row["distance_ma10_pct"] >= 0)
        )
        signals.append(bottom_signal)
        signal_pattern_scores[bottom_signal] = min(30.0, 22.0 + extras)

    contraction_base = (
        row["volume_ratio_1d_prior_10d"] <= 0.8
        and row["pct_chg"] >= -3
        and row["ma5_ma10_gap_pct"] <= 2
        and row["return_20d_pct"] > 0
        and row["distance_ma20_pct"] >= -2
        and row["drawdown_from_20d_high_pct"] >= -10
        and row["volume_ratio_5d_20d"] >= 1
    )
    if contraction_base:
        strong = row["volume_ratio_1d_prior_10d"] <= 0.7 and row["pct_chg"] >= -1.5
        signal = (
            "right_side_contraction_strong"
            if strong
            else "right_side_contraction_watch"
        )
        extras = (
            2 * int(row["volume_ratio_1d_prior_10d"] <= 0.7)
            + 2 * int(row["pct_chg"] >= 0)
            + int(row["ma5_ma10_gap_pct"] <= 1)
            + int(row["drawdown_from_20d_high_pct"] >= -5)
        )
        signals.append(signal)
        signal_pattern_scores[signal] = min(30.0, (26.0 if strong else 22.0) + extras)

    if not signals:
        return pd.Series(
            {
                "matched_signals": "",
                "primary_signal": "",
                "signal_count": 0,
                "pattern_score": 0.0,
                "volume_price_score": 0.0,
            }
        )

    primary = max(signals, key=lambda item: (signal_pattern_scores[item], -signals.index(item)))
    volume_price_options: list[float] = []
    for signal in signals:
        if signal.startswith("horizontal_") or signal.startswith("bottom_"):
            volume_points = 12 if row["volume_ratio_1d_prior_10d"] >= 2 else 8
            if row["pct_chg"] >= 3:
                price_points = 8
            elif row["pct_chg"] >= 0:
                price_points = 5
            elif signal.startswith("bottom_") and row["return_5d_pct"] > 0:
                price_points = 3
            else:
                price_points = 0
            volume_price_options.append(min(20.0, volume_points + price_points))
        else:
            volume_points = 10 if row["volume_ratio_1d_prior_10d"] <= 0.7 else 7
            if row["pct_chg"] >= 0:
                price_points = 10
            elif row["pct_chg"] >= -1.5:
                price_points = 8
            else:
                price_points = 5
            volume_price_options.append(min(20.0, volume_points + price_points))
    return pd.Series(
        {
            "matched_signals": "|".join(signals),
            "primary_signal": primary,
            "signal_count": len(signals),
            "pattern_score": round(max(signal_pattern_scores.values()), 2),
            "volume_price_score": round(max(volume_price_options), 2),
        }
    )


def stage_values(row: pd.Series) -> tuple[str, float]:
    early_right = (
        row["position_60d_pct"] <= 60
        and -5 <= row["return_20d_pct"] <= 20
        and row["ma5_slope_3d_pct"] > 0
    )
    second_wave = (
        row["return_60d_pct"] > 10
        and -10 <= row["drawdown_from_20d_high_pct"] <= 0
        and row["ma5_slope_3d_pct"] > 0
    )
    if early_right:
        return "early_right_side", 20.0
    if second_wave:
        return "second_wave_proxy", 16.0
    return "unclear", 10.0


def sector_points(percentile_value: float) -> float:
    if percentile_value >= 90:
        return 15.0
    if percentile_value >= 75:
        return 12.0
    if percentile_value >= 50:
        return 8.0
    return 0.0


def relative_strength_points(row: pd.Series) -> float:
    market_values = [row["return_5d_market_percentile"], row["return_20d_market_percentile"]]
    if all(pd.notna(value) for value in market_values):
        market_average = float(np.mean(market_values))
        market_points = 7 if market_average >= 80 else 5 if market_average >= 60 else 3 if market_average >= 50 else 1
    else:
        market_points = 0

    industry_value = row["return_5d_industry_percentile"]
    if pd.isna(industry_value):
        industry_points = 0
    else:
        industry_points = 4 if industry_value >= 80 else 3 if industry_value >= 70 else 1

    hit5 = row["hist_5d_target_hit_rate_percentile"]
    hit20 = row["hist_20d_target_hit_rate_percentile"]
    hit_flags = [value >= 50 for value in [hit5, hit20] if pd.notna(value)]
    hit_points = 2 if len(hit_flags) == 2 and all(hit_flags) else 1 if any(hit_flags) else 0
    atr_value = row["atr14_pct_percentile"]
    atr_points = 2 if pd.notna(atr_value) and atr_value >= 50 else 1 if pd.notna(atr_value) else 0
    elasticity_points = min(4, hit_points + atr_points)
    return float(min(15, market_points + industry_points + elasticity_points))


def build_reason(row: pd.Series) -> str:
    signal_text = "、".join(SIGNAL_LABELS[item] for item in row["matched_signals"].split("|") if item)
    return (
        f"匹配{signal_text}，量价得分{row['volume_price_score']:.2f}，"
        f"初步阶段为{row['stage_label']}。"
        f"所属{row['industry']}板块强度百分位{row['sector_rank_percentile']:.2f}，"
        f"5日行业强度百分位{row['return_5d_industry_percentile']:.2f}。"
    )


def special_stock_diagnostic(frame: pd.DataFrame, output: pd.DataFrame) -> dict[str, object]:
    target_code = "000636.SZ"
    matched = frame.loc[frame["ts_code"].eq(target_code)]
    if matched.empty:
        return {"ts_code": target_code, "present": False, "reason": "基础股票池不存在"}
    row = matched.iloc[0]
    common = {
        "板块进入前50%": bool(row["in_selected_sector"]),
        "量比>=1.5": bool(row["volume_ratio_1d_prior_10d"] >= 1.5),
        "5日收益>0": bool(row["return_5d_pct"] > 0),
        "MA5三日斜率>0": bool(row["ma5_slope_3d_pct"] > 0),
    }
    normal = {
        **common,
        "60日位置<=60": bool(row["position_60d_pct"] <= 60),
        "行业5日百分位>=70": bool(row["return_5d_industry_percentile"] >= 70),
        "距MA10>=-2%": bool(row["distance_ma10_pct"] >= -2),
    }
    relaxed = {
        **common,
        "板块百分位>=75": bool(row["sector_rank_percentile"] >= 75),
        "行业5日百分位>=80": bool(row["return_5d_industry_percentile"] >= 80),
        "60日位置<=75": bool(row["position_60d_pct"] <= 75),
    }
    return {
        "ts_code": target_code,
        "present": True,
        "name": str(row["name"]),
        "industry": str(row["industry"]),
        "sector_rank_percentile": None if pd.isna(row["sector_rank_percentile"]) else round(float(row["sector_rank_percentile"]), 4),
        "ordinary_bottom_right_side_match": all(normal.values()),
        "ordinary_conditions": normal,
        "ordinary_failed_conditions": [key for key, value in normal.items() if not value],
        "relaxed_bottom_right_side_match": all(relaxed.values()),
        "relaxed_conditions": relaxed,
        "relaxed_failed_conditions": [key for key, value in relaxed.items() if not value],
        "matched_signals": str(row["matched_signals"]),
        "total_score": round(float(row["total_score"]), 2),
        "in_output": bool(output["ts_code"].eq(target_code).any()),
    }


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    base, indicators, sectors, v1, market, config = load_inputs(args)
    trade_date = str(base["trade_date"].iloc[0])

    top_fraction = float(config["sector"]["sector_top_fraction"])
    eligible_sectors, selected_sectors, sector_cutoff = select_sectors(sectors, top_fraction)
    all_sector_names = set(sectors["industry"].astype(str))
    matched_sector_mask = base["industry"].astype(str).isin(all_sector_names)
    sector_match_count = int(matched_sector_mask.sum())
    unmatched_stock_count = int((~matched_sector_mask).sum())
    sector_match_rate = sector_match_count / len(base) * 100.0

    sector_metadata = eligible_sectors[
        ["industry", "sector_strength_score", "selection_rank", "sector_rank_percentile"]
    ].rename(
        columns={
            "sector_strength_score": "sector_score_original",
            "selection_rank": "sector_rank",
        }
    )
    frame = (
        base[BASE_COLUMNS]
        .merge(indicators[INDICATOR_COLUMNS].drop(columns=["trade_date"]), on="ts_code", how="left", validate="one_to_one")
        .merge(v1[V1_COLUMNS].drop(columns=["trade_date"]), on="ts_code", how="left", validate="one_to_one")
        .merge(sector_metadata, on="industry", how="left", validate="many_to_one")
    )
    frame.rename(columns={"segment": "board"}, inplace=True)
    selected_industries = set(selected_sectors["industry"].astype(str))
    frame["in_selected_sector"] = frame["industry"].astype(str).isin(selected_industries)

    frame["return_5d_market_percentile"] = percentile(frame["return_5d_pct"])
    frame["return_20d_market_percentile"] = percentile(frame["return_20d_pct"])
    frame["return_5d_industry_percentile"] = (
        frame.groupby("industry", dropna=False)["return_5d_pct"]
        .rank(pct=True, method="average")
        * 100.0
    )
    frame["hist_5d_target_hit_rate_percentile"] = percentile(
        frame["hist_5d_target_hit_rate_250d_pct"]
    )
    frame["hist_20d_target_hit_rate_percentile"] = percentile(
        frame["hist_20d_target_hit_rate_250d_pct"]
    )
    frame["atr14_pct_percentile"] = percentile(frame["atr14_pct"])

    # Evaluate the full current universe so the named-stock diagnostic remains
    # informative even when its sector is outside the retained top half.
    # Candidate output is still strictly gated by in_selected_sector below.
    signal_results = frame.apply(signal_and_scores, axis=1)
    frame = pd.concat([frame, signal_results], axis=1)
    stage_result = frame.apply(stage_values, axis=1, result_type="expand")
    frame[["stage_label", "stage_score"]] = stage_result
    frame["stage_confidence"] = "preliminary"
    frame["risk_review_pending"] = True
    frame["sector_score"] = frame["sector_rank_percentile"].map(sector_points)
    frame["relative_strength_elasticity_score"] = frame.apply(
        relative_strength_points, axis=1
    )
    score_columns = [
        "pattern_score",
        "volume_price_score",
        "stage_score",
        "sector_score",
        "relative_strength_elasticity_score",
    ]
    frame[score_columns] = frame[score_columns].round(2)
    frame["total_score"] = frame[score_columns].sum(axis=1).round(2)
    frame["market_state"] = str(market["market_state"])
    frame["market_score"] = float(market["market_score"])
    frame["market_bottom_status"] = str(market["market_bottom_status"])

    selected_frame = frame.loc[frame["in_selected_sector"]].copy()

    score_config = config["score_weights"]
    minimum_score = float(score_config["watch_candidate_min"])
    candidates = selected_frame.loc[
        selected_frame["signal_count"].ge(1) & selected_frame["total_score"].ge(minimum_score)
    ].copy()
    candidates["candidate_grade"] = np.select(
        [
            candidates["total_score"].ge(float(score_config["strong_candidate_min"])),
            candidates["total_score"].ge(float(score_config["normal_candidate_min"])),
        ],
        ["strong", "normal"],
        default="watch",
    )
    candidates["technical_reason"] = candidates.apply(build_reason, axis=1)
    candidates["manual_review_note"] = np.where(
        candidates["matched_signals"].str.contains("bottom_right_side_leader_relaxed", regex=False),
        "强势板块领先股采用宽松右侧条件，需人工确认是否属于底部启动。",
        "",
    )
    candidates.sort_values(
        [
            "total_score",
            "sector_rank_percentile",
            "return_5d_industry_percentile",
            "volume_ratio_1d_prior_10d",
        ],
        ascending=[False, False, False, False],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    candidates.reset_index(drop=True, inplace=True)
    candidates = candidates[OUTPUT_COLUMNS]

    if candidates["ts_code"].duplicated().any():
        raise RuntimeError("候选输出存在重复ts_code")
    if not candidates.empty:
        component_sum = candidates[score_columns].sum(axis=1).round(2)
        if not component_sum.eq(candidates["total_score"]).all():
            raise RuntimeError("候选分项和与总分不一致")
        if not candidates["total_score"].ge(minimum_score).all():
            raise RuntimeError("候选输出包含低于60分股票")
        if candidates["matched_signals"].eq("").any():
            raise RuntimeError("候选输出包含无信号股票")
        if not candidates["industry"].isin(selected_industries).all():
            raise RuntimeError("候选输出包含非前50%板块股票")

    signal_frame = selected_frame.loc[selected_frame["signal_count"].ge(1)].copy()
    subtype_counts: dict[str, int] = {}
    for signal in SIGNAL_LABELS:
        subtype_counts[signal] = int(
            signal_frame["matched_signals"].str.split("|").map(lambda values: signal in values).sum()
        )
    family_counts = {
        "horizontal_breakout": subtype_counts["horizontal_breakout_strong"]
        + subtype_counts["horizontal_breakout_watch"],
        "bottom_right_side": subtype_counts["bottom_right_side"]
        + subtype_counts["bottom_right_side_leader_relaxed"],
        "right_side_contraction": subtype_counts["right_side_contraction_strong"]
        + subtype_counts["right_side_contraction_watch"],
    }
    grade_counts = {
        grade: int(candidates["candidate_grade"].eq(grade).sum())
        for grade in ["strong", "normal", "watch"]
    }
    missing_columns = sorted(
        STOCK_FORMULA_FIELDS
        | {
            "sector_rank_percentile",
            "return_5d_market_percentile",
            "return_20d_market_percentile",
            "return_5d_industry_percentile",
            "hist_5d_target_hit_rate_percentile",
            "hist_20d_target_hit_rate_percentile",
            "atr14_pct_percentile",
        }
    )
    missing_stats = {column: int(selected_frame[column].isna().sum()) for column in missing_columns}
    excluded_fields = list(config["exclude_from_stock_scoring"])

    special_check = special_stock_diagnostic(frame, candidates)
    candidate_count_warning = None
    if len(candidates) == 0:
        candidate_count_warning = "候选数量为0；仅报告诊断，未调整阈值。"
    elif len(candidates) > 500:
        candidate_count_warning = "候选数量超过500；仅报告诊断，未调整阈值。"

    diagnostics = {
        "trade_date": trade_date,
        "market_state": market["market_state"],
        "market_score": market["market_score"],
        "market_bottom_status": market["market_bottom_status"],
        "hard_gate": market["hard_gate"],
        "sector_file": str(Path(args.sectors)),
        "eligible_sector_count": int(len(eligible_sectors)),
        "selected_top_fraction": top_fraction,
        "selected_sector_count": int(len(selected_sectors)),
        "sector_score_cutoff_inclusive": round(sector_cutoff, 6),
        "selected_sectors": selected_sectors[
            ["industry", "sector_strength_score", "selection_rank", "sector_rank_percentile"]
        ].to_dict(orient="records"),
        "stock_sector_match_count": sector_match_count,
        "stock_sector_unmatched_count": unmatched_stock_count,
        "stock_sector_match_rate_pct": round(sector_match_rate, 4),
        "selected_sector_stock_count": int(len(selected_frame)),
        "signal_family_counts": family_counts,
        "signal_subtype_counts": subtype_counts,
        "relaxed_leader_rule_count": subtype_counts["bottom_right_side_leader_relaxed"],
        "multi_signal_overlap_count": int(signal_frame["signal_count"].ge(2).sum()),
        "signaled_stock_count_before_score_filter": int(len(signal_frame)),
        "score_60_or_above_count": int(len(candidates)),
        "candidate_grade_counts": grade_counts,
        "score_distributions": {
            column: score_distribution(candidates, column)
            for column in [*score_columns, "total_score"]
        },
        "missing_field_counts_in_selected_sector_stocks": missing_stats,
        "field_mapping": FIELD_MAPPING,
        "excluded_indicators": excluded_fields,
        "excluded_formula_intersection": sorted(set(excluded_fields) & STOCK_FORMULA_FIELDS),
        "excluded_indicators_used_in_stock_filter_signal_score_or_sort": False,
        "all_associations_use_ts_code": True,
        "market_state_does_not_close_selection": True,
        "future_data_check": {
            "all_input_trade_dates": [trade_date],
            "selector_uses_latest_snapshot_only": True,
            "direct_history_file_read": False,
            "future_rows_used": 0,
        },
        "special_stock_check_000636_SZ": special_check,
        "candidate_count_warning": candidate_count_warning,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    output_path = Path(args.output)
    snapshot_path = Path(args.snapshot_dir) / f"raw_candidates_{trade_date}.csv"
    csv_buffer = io.StringIO(newline="")
    candidates.to_csv(csv_buffer, index=False, lineterminator="\n")
    csv_payload = csv_buffer.getvalue().encode("utf-8-sig")
    atomic_write_bytes(snapshot_path, csv_payload)
    atomic_write_bytes(output_path, csv_payload)
    diagnostics_payload = (
        json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    atomic_write_bytes(Path(args.diagnostics), diagnostics_payload)

    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False))
    print("\nTOP30")
    preview_columns = [
        "ts_code",
        "name",
        "industry",
        "primary_signal",
        "pattern_score",
        "volume_price_score",
        "stage_score",
        "sector_score",
        "relative_strength_elasticity_score",
        "total_score",
        "candidate_grade",
    ]
    print(candidates[preview_columns].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
