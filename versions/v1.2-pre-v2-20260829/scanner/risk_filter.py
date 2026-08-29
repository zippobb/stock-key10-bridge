#!/usr/bin/env python3
"""Apply V1 overheat, topping and conservative wave-risk rules to raw candidates."""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_RAW = "output/raw_candidates_latest.csv"
DEFAULT_INDICATORS = "output/indicators_latest.csv"
DEFAULT_V1 = "output/v1_features_latest.csv"
DEFAULT_HISTORY = "output/history_daily_latest.csv"
DEFAULT_MARKET = "output/market_environment_latest.json"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_TECHNICAL = "output/technical_candidates_latest.csv"
DEFAULT_VETOED = "output/risk_vetoed_latest.csv"
DEFAULT_SNAPSHOT_DIR = "data/candidates"
DEFAULT_DIAGNOSTICS = "output/risk_diagnostics_latest.json"

MIN_WAVE_HISTORY_DAYS = 60
H_FLAGS = ["H1_distance", "H2_ma_gap", "H3_return_5d", "H4_acceleration"]
T_FLAGS = ["T1_slope_deceleration", "T2_new_high_momentum_weakening", "T3_high_volume_stall_upper_shadow", "T4_support_break"]

EXTRA_COLUMNS = [
    "distance_ma20_atr",
    "ma5_ma10_gap_atr",
    "distance_ma20_percentile",
    "ma5_ma10_gap_percentile",
    "return_5d_risk_percentile",
    "ma5_slope_percentile",
    "ma5_slope_t_pct",
    "ma5_slope_t_minus_1_pct",
    "ma5_slope_t_minus_2_pct",
    "return_3d_current_pct",
    "return_3d_previous_pct",
    "prior_3d_low",
    *H_FLAGS,
    "overheat_flags",
    "overheat_flag_count",
    "overheat",
    *T_FLAGS,
    "top_risk_flags",
    "completed_up_wave_count",
    "approximate_up_wave_count",
    "current_wave_direction",
    "wave_risk_label",
    "wave_risk_evidence",
    "risk_action",
    "risk_adjustment",
    "risk_adjusted_score",
    "risk_adjusted_grade",
    "risk_reason",
    "fundamental_review_pending",
    "manual_confirmation_required",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成V1技术风险过滤结果")
    parser.add_argument("--raw", default=DEFAULT_RAW)
    parser.add_argument("--indicators", default=DEFAULT_INDICATORS)
    parser.add_argument("--v1", default=DEFAULT_V1)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--technical-output", default=DEFAULT_TECHNICAL)
    parser.add_argument("--veto-output", default=DEFAULT_VETOED)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--diagnostics", default=DEFAULT_DIAGNOSTICS)
    return parser.parse_args()


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def ensure_unique(frame: pd.DataFrame, key: str, label: str) -> None:
    duplicates = int(frame[key].duplicated().sum())
    if duplicates:
        raise ValueError(f"{label}存在重复{key}: {duplicates}")


def numeric(frame: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")


def percentile(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    return values.rank(method="average", pct=True) * 100.0


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    valid_denominator = denominator.where(denominator.gt(0))
    return numerator / valid_denominator


def atomic_write(path: Path, payload: bytes) -> None:
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


def csv_payload(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO(newline="")
    frame.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue().encode("utf-8-sig")


def score_distribution(series: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(float(values.min()), 2),
        "median": round(float(values.median()), 2),
        "max": round(float(values.max()), 2),
    }


def load_history_for_candidates(
    path: Path,
    candidate_codes: set[str],
    cutoff_date: str,
    lookback_days: int,
) -> tuple[pd.DataFrame, int, int]:
    pieces: list[pd.DataFrame] = []
    future_rows_excluded = 0
    source_rows_scanned = 0
    usecols = ["ts_code", "trade_date", "high", "low", "close"]
    for chunk in pd.read_csv(
        path,
        usecols=usecols,
        dtype={"ts_code": "string", "trade_date": "string"},
        chunksize=200_000,
    ):
        source_rows_scanned += len(chunk)
        code_mask = chunk["ts_code"].isin(candidate_codes)
        future_rows_excluded += int((code_mask & chunk["trade_date"].gt(cutoff_date)).sum())
        chosen = chunk.loc[code_mask & chunk["trade_date"].le(cutoff_date)].copy()
        if not chosen.empty:
            pieces.append(chosen)
    if not pieces:
        raise ValueError("历史表中没有候选股票在截止交易日前的数据")
    history = pd.concat(pieces, ignore_index=True)
    numeric(history, ["high", "low", "close"])
    history.drop_duplicates(["ts_code", "trade_date"], keep="last", inplace=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    history = history.groupby("ts_code", group_keys=False).tail(lookback_days).copy()
    if history["trade_date"].gt(cutoff_date).any():
        raise RuntimeError("未来历史行进入计算")
    return history, future_rows_excluded, source_rows_scanned


def history_metrics(group: pd.DataFrame, current_date: str) -> dict[str, object]:
    ordered = group.sort_values("trade_date", kind="stable").copy()
    ordered = ordered.loc[ordered["trade_date"].le(current_date)]
    close = ordered["close"]
    ma5 = close.rolling(5, min_periods=5).mean()
    slopes = (ma5 / ma5.shift(3) - 1.0) * 100.0
    recent_slopes = slopes.tail(3).tolist()
    while len(recent_slopes) < 3:
        recent_slopes.insert(0, np.nan)
    slope_t2, slope_t1, slope_t = recent_slopes
    return_3d_current = (
        (close.iloc[-1] / close.iloc[-4] - 1.0) * 100.0 if len(close) >= 4 else np.nan
    )
    return_3d_previous = (
        (close.iloc[-4] / close.iloc[-7] - 1.0) * 100.0 if len(close) >= 7 else np.nan
    )
    prior_3d_low = ordered["low"].iloc[-4:-1].min() if len(ordered) >= 4 else np.nan
    return {
        "history_days_used": int(len(ordered)),
        "history_latest_date": None if ordered.empty else str(ordered["trade_date"].iloc[-1]),
        "ma5_slope_t_pct": slope_t,
        "ma5_slope_t_minus_1_pct": slope_t1,
        "ma5_slope_t_minus_2_pct": slope_t2,
        "return_3d_current_pct": return_3d_current,
        "return_3d_previous_pct": return_3d_previous,
        "prior_3d_low": prior_3d_low,
    }


def analyze_wave(
    closes: pd.Series,
    return_60d_pct: float,
    drawdown_60d_pct: float,
    reversal_pct: float,
    reset_pct: float,
) -> dict[str, object]:
    prices = pd.to_numeric(closes, errors="coerce").dropna().tolist()
    if len(prices) < MIN_WAVE_HISTORY_DAYS:
        return {
            "completed_up_wave_count": 0,
            "approximate_up_wave_count": 0,
            "current_wave_direction": "uncertain",
            "wave_risk_label": "uncertain",
            "wave_risk_evidence": f"有效历史仅{len(prices)}日，少于{MIN_WAVE_HISTORY_DAYS}日。",
        }

    state = "seeking_rise"
    low = float(prices[0])
    high = float(prices[0])
    pullback_low = float(prices[0])
    anchor_high = float(prices[0])
    completed = 0
    reset_count = 0
    confirmed_any = False

    for raw_price in prices[1:]:
        price = float(raw_price)
        if price <= 0:
            continue
        if state == "seeking_rise":
            if price < low:
                low = price
            if low > 0 and (price / low - 1.0) * 100.0 >= reversal_pct:
                state = "up"
                high = price
                confirmed_any = True
        elif state == "up":
            if price > high:
                high = price
            pullback = (high - price) / high * 100.0 if high > 0 else 0.0
            if pullback >= reset_pct:
                completed = 0
                reset_count += 1
                state = "seeking_rise"
                low = price
            elif pullback >= reversal_pct:
                completed += 1
                state = "pullback"
                anchor_high = high
                pullback_low = price
        else:
            if price < pullback_low:
                pullback_low = price
            pullback = (
                (anchor_high - price) / anchor_high * 100.0 if anchor_high > 0 else 0.0
            )
            if pullback >= reset_pct:
                completed = 0
                reset_count += 1
                state = "seeking_rise"
                low = price
            elif pullback_low > 0 and (price / pullback_low - 1.0) * 100.0 >= reversal_pct:
                state = "up"
                high = price
                confirmed_any = True

    direction = "up" if state == "up" else "pullback" if state == "pullback" else "uncertain"
    approximate = completed + (1 if state == "up" else 0)
    reliable = confirmed_any and state != "seeking_rise"
    probable_third = (
        reliable
        and completed >= 2
        and state == "up"
        and approximate >= 3
        and pd.notna(return_60d_pct)
        and return_60d_pct > 20
        and pd.notna(drawdown_60d_pct)
        and drawdown_60d_pct >= -10
    )
    if not reliable:
        label = "uncertain"
    elif probable_third:
        label = "probable_third_wave"
    elif state == "up" and approximate <= 1:
        label = "first_wave_or_early"
    elif (state == "up" and approximate == 2) or (state == "pullback" and completed == 1):
        label = "possible_second_wave"
    else:
        label = "no_wave_risk"
    evidence = (
        f"最近{len(prices)}个实际交易日采用{reversal_pct:g}%反转、{reset_pct:g}%重置；"
        f"已完成上涨浪{completed}次，当前近似第{approximate}浪，方向{direction}，重置{reset_count}次；"
        f"60日收益{format_number(return_60d_pct)}%，距60日高点{format_number(drawdown_60d_pct)}%。"
    )
    return {
        "completed_up_wave_count": int(completed),
        "approximate_up_wave_count": int(approximate),
        "current_wave_direction": direction,
        "wave_risk_label": label,
        "wave_risk_evidence": evidence,
    }


def format_number(value: object) -> str:
    return "NaN" if pd.isna(value) else f"{float(value):.2f}"


def joined_flags(row: pd.Series, flags: list[str]) -> str:
    return "|".join(flag for flag in flags if bool(row[flag]))


def signal_family_counts(frame: pd.DataFrame) -> dict[str, int]:
    text = frame["matched_signals"].fillna("")
    return {
        "horizontal_breakout": int(text.str.contains("horizontal_breakout", regex=False).sum()),
        "bottom_right_side": int(text.str.contains("bottom_right_side", regex=False).sum()),
        "right_side_contraction": int(text.str.contains("right_side_contraction", regex=False).sum()),
    }


def reason_for_row(row: pd.Series) -> str:
    if row["risk_action"] == "VETO":
        reasons: list[str] = []
        if row["wave_risk_label"] == "probable_third_wave":
            reasons.append("保守波段识别为第三次或之后上涨阶段")
        if row["T4_support_break"]:
            reasons.append("收盘跌破MA5且同时跌破MA10或前三日低点")
        if row["overheat"] and row["T1_slope_deceleration"]:
            reasons.append("过热且MA5斜率连续减速")
        if row["overheat"] and row["T2_new_high_momentum_weakening"]:
            reasons.append("过热且新高动能减弱")
        return "VETO：" + "；".join(reasons) + "。"
    if row["risk_action"] == "DOWNGRADE":
        reasons = []
        if row["overheat"]:
            reasons.append(f"过热标志{row['overheat_flag_count']}项")
        if row["T3_high_volume_stall_upper_shadow"]:
            reasons.append("放量滞涨长上影")
        if row["T1_slope_deceleration"]:
            reasons.append("MA5斜率连续减速")
        if row["T2_new_high_momentum_weakening"]:
            reasons.append("新高动能减弱")
        return "DOWNGRADE：" + "；".join(reasons) + "，技术分下调。"
    return "PASS：未触发过热见顶组合、支撑跌破或高波段否决条件。"


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    paths = {
        "raw": Path(args.raw),
        "indicators": Path(args.indicators),
        "v1": Path(args.v1),
        "history": Path(args.history),
        "market": Path(args.market),
        "config": Path(args.config),
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"输入不存在: {label}={path}")

    raw = pd.read_csv(paths["raw"], dtype={"ts_code": "string", "trade_date": "string"})
    indicators = pd.read_csv(
        paths["indicators"], dtype={"ts_code": "string", "trade_date": "string"}
    )
    v1 = pd.read_csv(paths["v1"], dtype={"ts_code": "string", "trade_date": "string"})
    market = json.loads(paths["market"].read_text(encoding="utf-8"))
    config = json.loads(paths["config"].read_text(encoding="utf-8"))
    raw_columns = raw.columns.tolist()

    require_columns(
        raw,
        ["ts_code", "trade_date", "total_score", "candidate_grade", "matched_signals", "sector_rank_percentile"],
        "原始候选",
    )
    require_columns(
        indicators,
        [
            "ts_code", "trade_date", "high", "close", "ma5", "ma10", "atr14_pct",
            "distance_ma20_pct", "return_5d_pct", "return_60d_pct", "drawdown_from_20d_high_pct",
            "drawdown_from_60d_high_pct",
        ],
        "指标快照",
    )
    require_columns(
        v1,
        [
            "ts_code", "trade_date", "volume_ratio_1d_prior_10d", "prior_20d_high",
            "ma5_slope_3d_pct", "ma5_ma10_gap_pct", "upper_shadow_pct",
        ],
        "V1特征",
    )
    for label, frame in [("raw", raw), ("indicators", indicators), ("v1", v1)]:
        ensure_unique(frame, "ts_code", label)
    dates = {
        "raw": set(raw["trade_date"].dropna().astype(str)),
        "indicators": set(indicators["trade_date"].dropna().astype(str)),
        "v1": set(v1["trade_date"].dropna().astype(str)),
        "market": {str(market.get("trade_date"))},
    }
    all_dates = set().union(*dates.values())
    if any(len(values) != 1 for values in dates.values()) or len(all_dates) != 1:
        raise ValueError(f"输入交易日不一致: {dates}")
    trade_date = next(iter(all_dates))
    if market.get("hard_gate") is not False:
        raise ValueError("hard_gate必须为false")

    overheat_cfg = config["overheat"]
    wave_cfg = config["wave"]
    downgrade_points = float(config["risk"]["risk_downgrade_points"])
    required_parameters = [
        "overheat_min_flags", "distance_ma20_atr_warning", "ma5_ma10_gap_atr_warning",
        "distance_cross_percentile_warning", "ma_gap_cross_percentile_warning",
        "return_5d_warning_pct", "return_5d_cross_percentile_warning",
        "slope_cross_percentile_warning", "upper_shadow_warning_pct", "high_volume_ratio",
        "low_gain_warning_pct", "support_buffer_pct",
    ]
    missing_parameters = [key for key in required_parameters if key not in overheat_cfg]
    if missing_parameters:
        raise ValueError(f"配置缺少过热风险参数: {missing_parameters}")

    numeric(
        indicators,
        [
            "high", "close", "ma5", "ma10", "atr14_pct", "distance_ma20_pct",
            "return_5d_pct", "return_60d_pct", "drawdown_from_20d_high_pct",
            "drawdown_from_60d_high_pct",
        ],
    )
    numeric(
        v1,
        [
            "volume_ratio_1d_prior_10d", "prior_20d_high", "ma5_slope_3d_pct",
            "ma5_ma10_gap_pct", "upper_shadow_pct",
        ],
    )
    market_risk = indicators[["ts_code", "distance_ma20_pct", "return_5d_pct"]].merge(
        v1[["ts_code", "ma5_ma10_gap_pct", "ma5_slope_3d_pct"]],
        on="ts_code", how="inner", validate="one_to_one",
    )
    market_risk["distance_ma20_percentile"] = percentile(market_risk["distance_ma20_pct"])
    market_risk["ma5_ma10_gap_percentile"] = percentile(market_risk["ma5_ma10_gap_pct"])
    market_risk["return_5d_risk_percentile"] = percentile(market_risk["return_5d_pct"])
    market_risk["ma5_slope_percentile"] = percentile(market_risk["ma5_slope_3d_pct"])

    indicator_add = indicators[
        [
            "ts_code", "high", "ma5", "ma10", "distance_ma20_pct", "return_60d_pct",
            "drawdown_from_20d_high_pct", "drawdown_from_60d_high_pct",
        ]
    ]
    v1_add = v1[
        ["ts_code", "prior_20d_high", "ma5_slope_3d_pct", "ma5_ma10_gap_pct", "upper_shadow_pct"]
    ]
    percentile_add = market_risk[
        [
            "ts_code", "distance_ma20_percentile", "ma5_ma10_gap_percentile",
            "return_5d_risk_percentile", "ma5_slope_percentile",
        ]
    ]
    frame = raw.merge(indicator_add, on="ts_code", how="left", validate="one_to_one")
    frame = frame.merge(v1_add, on="ts_code", how="left", validate="one_to_one")
    frame = frame.merge(percentile_add, on="ts_code", how="left", validate="one_to_one")
    numeric(
        frame,
        [
            "close", "pct_chg", "total_score", "sector_rank_percentile", "atr14_pct",
            "volume_ratio_1d_prior_10d", "return_5d_pct", "return_20d_pct", "high",
            "ma5", "ma10", "distance_ma20_pct", "return_60d_pct",
            "drawdown_from_20d_high_pct", "drawdown_from_60d_high_pct", "prior_20d_high",
            "ma5_slope_3d_pct", "ma5_ma10_gap_pct", "upper_shadow_pct",
            "distance_ma20_percentile", "ma5_ma10_gap_percentile",
            "return_5d_risk_percentile", "ma5_slope_percentile",
        ],
    )

    history, future_rows_excluded, source_rows_scanned = load_history_for_candidates(
        paths["history"], set(frame["ts_code"].dropna().astype(str)), trade_date,
        int(wave_cfg["wave_lookback_days"]),
    )
    history_metric_records: list[dict[str, object]] = []
    wave_records: list[dict[str, object]] = []
    lookup = frame.set_index("ts_code")
    for ts_code, group in history.groupby("ts_code", sort=False):
        metrics = history_metrics(group, trade_date)
        metrics["ts_code"] = ts_code
        history_metric_records.append(metrics)
        stock = lookup.loc[ts_code]
        wave = analyze_wave(
            group["close"], stock["return_60d_pct"], stock["drawdown_from_60d_high_pct"],
            float(wave_cfg["wave_reversal_pct"]), float(wave_cfg["wave_reset_pullback_pct"]),
        )
        wave["ts_code"] = ts_code
        wave_records.append(wave)
    frame = frame.merge(pd.DataFrame(history_metric_records), on="ts_code", how="left", validate="one_to_one")
    frame = frame.merge(pd.DataFrame(wave_records), on="ts_code", how="left", validate="one_to_one")
    missing_wave = frame["wave_risk_label"].isna()
    frame.loc[missing_wave, "completed_up_wave_count"] = 0
    frame.loc[missing_wave, "approximate_up_wave_count"] = 0
    frame.loc[missing_wave, "current_wave_direction"] = "uncertain"
    frame.loc[missing_wave, "wave_risk_label"] = "uncertain"
    frame.loc[missing_wave, "wave_risk_evidence"] = "截止候选交易日没有足够历史数据，波段状态不确定。"

    frame["distance_ma20_atr"] = safe_ratio(frame["distance_ma20_pct"].clip(lower=0), frame["atr14_pct"])
    frame["ma5_ma10_gap_atr"] = safe_ratio(frame["ma5_ma10_gap_pct"], frame["atr14_pct"])
    frame["H1_distance"] = (
        frame["distance_ma20_atr"].ge(float(overheat_cfg["distance_ma20_atr_warning"]))
        | frame["distance_ma20_percentile"].ge(float(overheat_cfg["distance_cross_percentile_warning"]))
    )
    frame["H2_ma_gap"] = (
        frame["ma5_ma10_gap_atr"].ge(float(overheat_cfg["ma5_ma10_gap_atr_warning"]))
        | frame["ma5_ma10_gap_percentile"].ge(float(overheat_cfg["ma_gap_cross_percentile_warning"]))
    )
    frame["H3_return_5d"] = (
        frame["return_5d_pct"].ge(float(overheat_cfg["return_5d_warning_pct"]))
        | frame["return_5d_risk_percentile"].ge(float(overheat_cfg["return_5d_cross_percentile_warning"]))
    )
    positive_three_slopes = (
        frame[["ma5_slope_t_pct", "ma5_slope_t_minus_1_pct", "ma5_slope_t_minus_2_pct"]]
        .gt(0).all(axis=1)
    )
    frame["H4_acceleration"] = (
        positive_three_slopes
        & frame["ma5_slope_t_pct"].gt(frame["ma5_slope_t_minus_1_pct"])
        & frame["ma5_slope_t_minus_1_pct"].gt(frame["ma5_slope_t_minus_2_pct"])
        & frame["ma5_slope_percentile"].ge(float(overheat_cfg["slope_cross_percentile_warning"]))
    )
    for flag in H_FLAGS:
        frame[flag] = frame[flag].fillna(False).astype(bool)
    frame["overheat_flags"] = frame.apply(lambda row: joined_flags(row, H_FLAGS), axis=1)
    frame["overheat_flag_count"] = frame[H_FLAGS].sum(axis=1).astype(int)
    frame["overheat"] = frame["overheat_flag_count"].ge(int(overheat_cfg["overheat_min_flags"]))

    frame["T1_slope_deceleration"] = (
        positive_three_slopes
        & frame["ma5_slope_t_pct"].lt(frame["ma5_slope_t_minus_1_pct"])
        & frame["ma5_slope_t_minus_1_pct"].lt(frame["ma5_slope_t_minus_2_pct"])
        & frame["drawdown_from_20d_high_pct"].ge(-3)
    )
    frame["T2_new_high_momentum_weakening"] = (
        (frame["high"].ge(frame["prior_20d_high"]) | frame["close"].ge(frame["prior_20d_high"]))
        & frame["ma5_slope_t_pct"].lt(frame["ma5_slope_t_minus_1_pct"])
        & frame["return_3d_current_pct"].lt(frame["return_3d_previous_pct"])
    )
    frame["T3_high_volume_stall_upper_shadow"] = (
        frame["volume_ratio_1d_prior_10d"].ge(float(overheat_cfg["high_volume_ratio"]))
        & frame["pct_chg"].le(float(overheat_cfg["low_gain_warning_pct"]))
        & frame["upper_shadow_pct"].ge(float(overheat_cfg["upper_shadow_warning_pct"]))
    )
    support_multiplier = 1.0 - float(overheat_cfg["support_buffer_pct"]) / 100.0
    frame["T4_support_break"] = (
        frame["close"].lt(frame["ma5"] * support_multiplier)
        & (
            frame["close"].lt(frame["ma10"] * support_multiplier)
            | frame["close"].lt(frame["prior_3d_low"] * support_multiplier)
        )
    )
    for flag in T_FLAGS:
        frame[flag] = frame[flag].fillna(False).astype(bool)
    frame["top_risk_flags"] = frame.apply(lambda row: joined_flags(row, T_FLAGS), axis=1)

    veto = (
        frame["wave_risk_label"].eq("probable_third_wave")
        | frame["T4_support_break"]
        | (frame["overheat"] & frame["T1_slope_deceleration"])
        | (frame["overheat"] & frame["T2_new_high_momentum_weakening"])
        | (frame["overheat"] & frame["T4_support_break"])
    )
    downgrade = (
        ~veto
        & (
            frame["overheat"]
            | frame["T3_high_volume_stall_upper_shadow"]
            | frame["T1_slope_deceleration"]
            | frame["T2_new_high_momentum_weakening"]
        )
    )
    frame["risk_action"] = np.select([veto, downgrade], ["VETO", "DOWNGRADE"], default="PASS")
    frame["risk_adjustment"] = np.where(frame["risk_action"].eq("DOWNGRADE"), -downgrade_points, 0.0)
    frame["risk_adjusted_score"] = (frame["total_score"] + frame["risk_adjustment"]).round(2)
    grade_map = {"strong": "normal", "normal": "watch", "watch": "watch_risk"}
    frame["risk_adjusted_grade"] = frame["candidate_grade"]
    down_mask = frame["risk_action"].eq("DOWNGRADE")
    frame.loc[down_mask, "risk_adjusted_grade"] = frame.loc[down_mask, "candidate_grade"].map(grade_map)
    frame.loc[frame["risk_action"].eq("VETO"), "risk_adjusted_grade"] = "vetoed"
    frame["risk_reason"] = frame.apply(reason_for_row, axis=1)
    frame["fundamental_review_pending"] = True
    frame["manual_confirmation_required"] = True

    output_columns = raw_columns + EXTRA_COLUMNS
    technical = frame.loc[~frame["risk_action"].eq("VETO"), output_columns].copy()
    vetoed = frame.loc[frame["risk_action"].eq("VETO"), output_columns].copy()
    technical.sort_values(
        ["risk_adjusted_score", "total_score", "sector_rank_percentile"],
        ascending=[False, False, False], na_position="last", kind="stable", inplace=True,
    )
    vetoed.sort_values(
        ["total_score", "sector_rank_percentile"], ascending=[False, False],
        na_position="last", kind="stable", inplace=True,
    )
    technical.reset_index(drop=True, inplace=True)
    vetoed.reset_index(drop=True, inplace=True)

    if len(technical) + len(vetoed) != len(raw):
        raise RuntimeError("技术候选与VETO数量之和不等于原始候选")
    if technical["ts_code"].duplicated().any() or vetoed["ts_code"].duplicated().any():
        raise RuntimeError("输出存在重复ts_code")
    if set(technical["ts_code"]) & set(vetoed["ts_code"]):
        raise RuntimeError("技术候选与VETO名单交叉")
    if technical["risk_action"].eq("VETO").any():
        raise RuntimeError("VETO股票进入技术候选")
    expected_adjusted = (frame["total_score"] + frame["risk_adjustment"]).round(2)
    if not expected_adjusted.eq(frame["risk_adjusted_score"]).all():
        raise RuntimeError("risk_adjusted_score计算错误")
    uncertain_solo_veto = frame[
        frame["wave_risk_label"].eq("uncertain")
        & frame["risk_action"].eq("VETO")
        & ~frame["T4_support_break"]
        & ~(frame["overheat"] & frame["T1_slope_deceleration"])
        & ~(frame["overheat"] & frame["T2_new_high_momentum_weakening"])
    ]
    if not uncertain_solo_veto.empty:
        raise RuntimeError("uncertain波段单独造成否决")

    veto_reason_masks = {
        "probable_third_wave": frame["wave_risk_label"].eq("probable_third_wave"),
        "T4_support_break": frame["T4_support_break"],
        "overheat_and_T1": frame["overheat"] & frame["T1_slope_deceleration"],
        "overheat_and_T2": frame["overheat"] & frame["T2_new_high_momentum_weakening"],
        "overheat_and_T4": frame["overheat"] & frame["T4_support_break"],
    }
    veto_reason_counts = {
        key: int((mask & frame["risk_action"].eq("VETO")).sum())
        for key, mask in veto_reason_masks.items()
    }
    after_grade_counts = {
        grade: int(technical["risk_adjusted_grade"].eq(grade).sum())
        for grade in ["strong", "normal", "watch", "watch_risk"]
    }
    missing_columns = [
        "distance_ma20_atr", "ma5_ma10_gap_atr", "distance_ma20_percentile",
        "ma5_ma10_gap_percentile", "return_5d_risk_percentile", "ma5_slope_percentile",
        "ma5_slope_t_pct", "ma5_slope_t_minus_1_pct", "ma5_slope_t_minus_2_pct",
        "return_3d_current_pct", "return_3d_previous_pct", "prior_3d_low",
        "upper_shadow_pct", "prior_20d_high", "ma5", "ma10", "return_60d_pct",
        "drawdown_from_60d_high_pct",
    ]
    missing_counts = {column: int(frame[column].isna().sum()) for column in missing_columns}
    probable_count = int(frame["wave_risk_label"].eq("probable_third_wave").sum())
    veto_count = int(frame["risk_action"].eq("VETO").sum())
    warnings: list[str] = []
    if veto_count > len(frame) * 0.5:
        warnings.append("VETO超过原始候选的50%，未调整规则，需人工复核。")
    if probable_count > len(frame) * 0.3:
        warnings.append("probable_third_wave超过原始候选的30%，未调整规则，需人工复核。")

    raw_top30 = raw.sort_values(
        ["total_score", "sector_rank_percentile"], ascending=[False, False], kind="stable"
    ).head(30)
    top30_outcome = raw_top30[["ts_code", "name", "total_score"]].merge(
        frame[["ts_code", "risk_action", "risk_adjusted_score", "risk_adjusted_grade", "risk_reason"]],
        on="ts_code", how="left", validate="one_to_one",
    ).to_dict(orient="records")
    diagnostics = {
        "trade_date": trade_date,
        "market_state": market.get("market_state"),
        "market_score": market.get("market_score"),
        "market_state_used_as_veto": False,
        "raw_candidate_count": int(len(raw)),
        "action_counts": {
            action: int(frame["risk_action"].eq(action).sum())
            for action in ["PASS", "DOWNGRADE", "VETO"]
        },
        "overheat_flag_trigger_counts": {flag: int(frame[flag].sum()) for flag in H_FLAGS},
        "overheat_count": int(frame["overheat"].sum()),
        "top_risk_trigger_counts": {flag: int(frame[flag].sum()) for flag in T_FLAGS},
        "probable_third_wave_count": probable_count,
        "uncertain_wave_count": int(frame["wave_risk_label"].eq("uncertain").sum()),
        "risk_adjusted_grade_counts_non_veto": after_grade_counts,
        "signal_family_counts_before": signal_family_counts(frame),
        "signal_family_counts_after_non_veto": signal_family_counts(technical),
        "veto_reason_counts": veto_reason_counts,
        "score_distributions": {
            "before_total_score": score_distribution(frame["total_score"]),
            "after_non_veto_risk_adjusted_score": score_distribution(technical["risk_adjusted_score"]),
        },
        "missing_field_counts": missing_counts,
        "future_data_check": {
            "candidate_trade_date": trade_date,
            "history_filter_expression": "trade_date <= candidate_trade_date",
            "history_rows_scanned": int(source_rows_scanned),
            "future_candidate_history_rows_excluded": int(future_rows_excluded),
            "max_history_date_used": None if history.empty else str(history["trade_date"].max()),
            "future_rows_used": 0,
            "all_history_group_latest_dates_not_after_candidate": bool(
                history.groupby("ts_code")["trade_date"].max().le(trade_date).all()
            ),
        },
        "validation": {
            "technical_plus_veto_equals_raw": len(technical) + len(vetoed) == len(raw),
            "technical_ts_code_duplicates": int(technical["ts_code"].duplicated().sum()),
            "veto_ts_code_duplicates": int(vetoed["ts_code"].duplicated().sum()),
            "cross_list_overlap": int(len(set(technical["ts_code"]) & set(vetoed["ts_code"]))),
            "veto_in_technical": int(technical["risk_action"].eq("VETO").sum()),
            "risk_adjusted_score_mismatch": int((~expected_adjusted.eq(frame["risk_adjusted_score"])).sum()),
            "uncertain_wave_solo_veto_count": int(len(uncertain_solo_veto)),
            "market_state_caused_veto": False,
            "all_associations_use_ts_code": True,
        },
        "configured_thresholds": {
            "overheat": overheat_cfg,
            "wave": wave_cfg,
            "risk_downgrade_points": downgrade_points,
        },
        "raw_top30_outcomes": top30_outcome,
        "warnings": warnings,
        "elapsed_seconds_before_write": round(time.perf_counter() - started, 3),
    }

    technical_latest = Path(args.technical_output)
    veto_latest = Path(args.veto_output)
    snapshot_dir = Path(args.snapshot_dir)
    technical_snapshot = snapshot_dir / f"technical_candidates_{trade_date}.csv"
    veto_snapshot = snapshot_dir / f"risk_vetoed_{trade_date}.csv"
    technical_bytes = csv_payload(technical)
    veto_bytes = csv_payload(vetoed)
    atomic_write(technical_snapshot, technical_bytes)
    atomic_write(technical_latest, technical_bytes)
    atomic_write(veto_snapshot, veto_bytes)
    atomic_write(veto_latest, veto_bytes)
    diagnostics_bytes = (
        json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    atomic_write(Path(args.diagnostics), diagnostics_bytes)

    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False))
    print("\nTOP30_AFTER_RISK")
    print(
        technical[
            ["ts_code", "name", "total_score", "risk_action", "risk_adjustment", "risk_adjusted_score", "risk_adjusted_grade", "overheat_flags", "top_risk_flags", "wave_risk_label"]
        ].head(30).to_string(index=False)
    )
    print("\nALL_VETO")
    if vetoed.empty:
        print("NONE")
    else:
        print(
            vetoed[
                ["ts_code", "name", "total_score", "overheat_flags", "top_risk_flags", "wave_risk_label", "risk_reason"]
            ].to_string(index=False)
        )
    print("\nRAW_TOP30_OUTCOMES")
    print(pd.DataFrame(top30_outcome).to_string(index=False))


if __name__ == "__main__":
    main()
