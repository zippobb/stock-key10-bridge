#!/usr/bin/env python3
"""Build branch-1 technical shortlists strictly inside the 246-stock observation pool."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_OBSERVATION = "config/observation_pool.csv"
DEFAULT_FOCUS = "config/focus_pool.csv"
DEFAULT_BASE = "output/base_universe_latest.csv"
DEFAULT_HISTORY = "output/history_daily_latest.csv"
DEFAULT_INDICATORS = "output/indicators_latest.csv"
DEFAULT_SECTORS = "output/sector_indicators_latest.csv"
DEFAULT_V1 = "output/v1_features_latest.csv"
DEFAULT_MARKET = "output/market_environment_latest.json"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_SHORTLIST = "output/branch1_technical_shortlist_latest.csv"
DEFAULT_TOP10 = "output/branch1_technical_top10_preview_latest.csv"
DEFAULT_SNAPSHOT_DIR = "data/branch1"

EXPECTED_OBSERVATION_SIZE = 246
EXPECTED_FOCUS_SIZE = 87
EXCLUDED_STOCK_FIELD = "three_days_no_break_prior_20d_low"

SIGNAL_LABELS = {
    "horizontal_breakout_strong": "横盘后强放量突破",
    "horizontal_breakout_watch": "横盘后放量突破观察",
    "bottom_right_side": "底部放量转入右侧初期",
    "bottom_right_side_leader_relaxed": "强势板块领先股宽松右侧初期",
    "right_side_contraction_strong": "右侧初期强缩量不跌",
    "right_side_contraction_watch": "右侧初期缩量不跌观察",
}

FORMULA_FIELDS = {
    "range_20d_pct", "center_band_ratio_20d_pct", "ma20_slope_5d_pct",
    "volume_ratio_1d_prior_10d", "breakout_vs_prior_20d_high_pct", "pct_chg",
    "return_5d_pct", "return_20d_pct", "return_60d_pct", "ma5_slope_3d_pct",
    "ma5_prev_slope_3d_pct", "ma5_slope_change_pct", "position_60d_pct",
    "distance_ma10_pct", "ma5_ma10_gap_pct", "distance_ma20_pct",
    "drawdown_from_20d_high_pct", "volume_ratio_5d_20d", "atr14_pct",
    "hist_5d_target_hit_rate_250d_pct", "hist_20d_target_hit_rate_250d_pct",
    "upper_shadow_pct", "open", "high", "low", "close", "ma5", "ma10",
}

OUTPUT_COLUMNS = [
    "rank", "trade_date", "ts_code", "name", "is_focus", "close", "sector",
    "total_score", "pattern_score", "volume_price_score", "stage_score",
    "sector_score", "relative_strength_score", "historical_elasticity_percentile",
    "matched_signal", "primary_signal", "stage_label", "market_state", "market_score",
    "market_bottom_status", "risk_status", "risk_reasons", "return_1d_pct",
    "return_5d_pct", "return_20d_pct", "close_vs_ma20_atr",
    "ma5_ma10_gap_atr", "ma5_slope_3d_pct", "ma5_slope_change_pct",
    "upper_shadow_pct", "overheat_signal_count", "return_5d_observation_percentile",
    "distance_ma20_atr_observation_percentile", "ma_gap_atr_observation_percentile",
    "return_5d_observation_percentile_strength", "return_20d_observation_percentile",
    "return_5d_industry_percentile", "sector_strength_score", "sector_rank",
    "sector_rank_percentile", "hist_5d_target_hit_rate_250d_pct",
    "hist_20d_target_hit_rate_250d_pct", "atr14_pct", "fundamental_review_pending",
    "selection_reasons",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observation", default=DEFAULT_OBSERVATION)
    parser.add_argument("--focus", default=DEFAULT_FOCUS)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--history", default=DEFAULT_HISTORY)
    parser.add_argument("--indicators", default=DEFAULT_INDICATORS)
    parser.add_argument("--sectors", default=DEFAULT_SECTORS)
    parser.add_argument("--v1", default=DEFAULT_V1)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--shortlist", default=DEFAULT_SHORTLIST)
    parser.add_argument("--top10", default=DEFAULT_TOP10)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--next-trade-date", default="")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def ensure_unique(frame: pd.DataFrame, column: str, label: str) -> None:
    duplicate_count = int(frame[column].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"{label}的{column}重复{duplicate_count}行")


def normalize_date(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace(".0", "", regex=False).str.strip()


def percentile(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, method="average") * 100.0


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    result = num / den.where(den.gt(0))
    return result.replace([np.inf, -np.inf], np.nan)


def json_scalar(value: object) -> object:
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def backup_existing(paths: list[Path]) -> list[str]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        backup = path.with_name(f"{path.name}.bak.{stamp}")
        with path.open("rb") as source:
            payload = source.read()
        atomic_write(backup, payload)
        backups.append(str(backup))
    return backups


def csv_payload(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n", float_format="%.6f")
    return buffer.getvalue().encode("utf-8-sig")


def load_history_metrics(path: Path, pool_codes: set[str], cutoff: str) -> tuple[pd.DataFrame, dict[str, object]]:
    pieces: list[pd.DataFrame] = []
    source_rows = 0
    future_rows = 0
    source_max = ""
    required = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]
    for chunk in pd.read_csv(path, dtype={"ts_code": "string", "trade_date": "string"}, chunksize=150000, low_memory=False):
        require_columns(chunk, required, "历史行情")
        source_rows += len(chunk)
        chunk["trade_date"] = normalize_date(chunk["trade_date"])
        if not chunk.empty:
            source_max = max(source_max, str(chunk["trade_date"].max()))
        future_rows += int(chunk["trade_date"].gt(cutoff).sum())
        selected = chunk.loc[chunk["ts_code"].isin(pool_codes) & chunk["trade_date"].le(cutoff), required]
        if not selected.empty:
            pieces.append(selected.copy())
    if not pieces:
        raise ValueError("历史行情中没有观察池数据")
    history = pd.concat(pieces, ignore_index=True)
    for column in ["open", "high", "low", "close", "vol", "amount"]:
        history[column] = pd.to_numeric(history[column], errors="coerce")
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    duplicate_count = int(history.duplicated(["ts_code", "trade_date"]).sum())
    if duplicate_count:
        raise ValueError(f"观察池历史ts_code/trade_date重复{duplicate_count}行")
    records: list[dict[str, object]] = []
    for ts_code, group in history.groupby("ts_code", sort=False):
        ordered = group.sort_values("trade_date", kind="stable")
        close = ordered["close"]
        ma5 = close.rolling(5, min_periods=5).mean()
        slopes = (ma5 / ma5.shift(3) - 1.0) * 100.0
        records.append({
            "ts_code": str(ts_code),
            "history_days_branch1": int(len(ordered)),
            "history_latest_date_branch1": str(ordered["trade_date"].iloc[-1]),
            "ma5_slope_t_hist_pct": slopes.iloc[-1] if len(slopes) else np.nan,
            "ma5_slope_t_minus_1_hist_pct": slopes.iloc[-2] if len(slopes) >= 2 else np.nan,
            "ma5_slope_t_minus_2_hist_pct": slopes.iloc[-3] if len(slopes) >= 3 else np.nan,
            "prior_3d_low": ordered["low"].iloc[-4:-1].min() if len(ordered) >= 4 else np.nan,
        })
    metrics = pd.DataFrame(records)
    guard = {
        "source_rows_scanned": source_rows,
        "source_max_trade_date": source_max,
        "cutoff_trade_date": cutoff,
        "future_rows_in_source": future_rows,
        "future_rows_used": 0,
        "current_day_excluded_from_prior_3d_low": True,
        "per_stock_actual_trading_days": True,
    }
    if future_rows:
        raise ValueError(f"历史源包含候选日之后的{future_rows}行，按安全边界停止")
    return metrics, guard


def signal_and_scores(row: pd.Series) -> pd.Series:
    signals: list[str] = []
    pattern: dict[str, float] = {}
    horizontal = (
        row["range_20d_pct"] <= 18
        and row["center_band_ratio_20d_pct"] >= 70
        and abs(row["ma20_slope_5d_pct"]) <= 3
        and row["volume_ratio_1d_prior_10d"] >= 1.5
        and row["breakout_vs_prior_20d_high_pct"] >= -2
        and row["return_1d_pct"] >= 0
    )
    if horizontal:
        strong = row["volume_ratio_1d_prior_10d"] >= 2 and row["breakout_vs_prior_20d_high_pct"] >= 0
        signal = "horizontal_breakout_strong" if strong else "horizontal_breakout_watch"
        extras = (2 * int(row["volume_ratio_1d_prior_10d"] >= 2)
                  + 2 * int(row["breakout_vs_prior_20d_high_pct"] >= 0)
                  + int(row["range_20d_pct"] <= 12)
                  + int(row["center_band_ratio_20d_pct"] >= 80))
        signals.append(signal)
        pattern[signal] = min(30.0, (26.0 if strong else 22.0) + extras)

    common = (row["volume_ratio_1d_prior_10d"] >= 1.5
              and row["return_5d_pct"] > 0 and row["ma5_slope_3d_pct"] > 0)
    normal = (common and row["position_60d_pct"] <= 60
              and row["return_5d_industry_percentile"] >= 70
              and row["distance_ma10_pct"] >= -2)
    relaxed = (common and row["sector_rank_percentile"] >= 75
               and row["return_5d_industry_percentile"] >= 80
               and row["position_60d_pct"] <= 75)
    bottom_signal = "bottom_right_side" if normal else "bottom_right_side_leader_relaxed" if relaxed else ""
    if bottom_signal:
        extras = (2 * int(row["sector_rank_percentile"] >= 90)
                  + 2 * int(row["return_5d_industry_percentile"] >= 90)
                  + int(row["position_60d_pct"] <= 40)
                  + int(row["volume_ratio_1d_prior_10d"] >= 2)
                  + int(row["distance_ma10_pct"] >= 0))
        signals.append(bottom_signal)
        pattern[bottom_signal] = min(30.0, 22.0 + extras)

    contraction = (row["volume_ratio_1d_prior_10d"] <= 0.8
                   and row["return_1d_pct"] >= -3 and row["ma5_ma10_gap_pct"] <= 2
                   and row["return_20d_pct"] > 0 and row["distance_ma20_pct"] >= -2
                   and row["drawdown_from_20d_high_pct"] >= -10
                   and row["volume_ratio_5d_20d"] >= 1)
    if contraction:
        strong = row["volume_ratio_1d_prior_10d"] <= 0.7 and row["return_1d_pct"] >= -1.5
        signal = "right_side_contraction_strong" if strong else "right_side_contraction_watch"
        extras = (2 * int(row["volume_ratio_1d_prior_10d"] <= 0.7)
                  + 2 * int(row["return_1d_pct"] >= 0)
                  + int(row["ma5_ma10_gap_pct"] <= 1)
                  + int(row["drawdown_from_20d_high_pct"] >= -5))
        signals.append(signal)
        pattern[signal] = min(30.0, (26.0 if strong else 22.0) + extras)

    if not signals:
        return pd.Series({"matched_signal": "", "primary_signal": "", "signal_count": 0,
                          "pattern_score": 0.0, "volume_price_score": 0.0})
    primary = max(signals, key=lambda item: (pattern[item], -signals.index(item)))
    volume_options: list[float] = []
    for signal in signals:
        if signal.startswith("horizontal_") or signal.startswith("bottom_"):
            volume_points = 12 if row["volume_ratio_1d_prior_10d"] >= 2 else 8
            if row["return_1d_pct"] >= 3:
                price_points = 8
            elif row["return_1d_pct"] >= 0:
                price_points = 5
            elif signal.startswith("bottom_") and row["return_5d_pct"] > 0:
                price_points = 3
            else:
                price_points = 0
        else:
            volume_points = 10 if row["volume_ratio_1d_prior_10d"] <= 0.7 else 7
            price_points = 10 if row["return_1d_pct"] >= 0 else 8 if row["return_1d_pct"] >= -1.5 else 5
        volume_options.append(min(20.0, volume_points + price_points))
    return pd.Series({"matched_signal": "|".join(signals), "primary_signal": primary,
                      "signal_count": len(signals), "pattern_score": round(max(pattern.values()), 2),
                      "volume_price_score": round(max(volume_options), 2)})


def stage_values(row: pd.Series) -> tuple[str, float]:
    early = (row["position_60d_pct"] <= 60 and -5 <= row["return_20d_pct"] <= 20
             and row["ma5_slope_3d_pct"] > 0)
    second = (row["return_60d_pct"] > 10 and -10 <= row["drawdown_from_20d_high_pct"] <= 0
              and row["ma5_slope_3d_pct"] > 0)
    if early:
        return "early_right_side", 20.0
    if second:
        return "second_wave_proxy", 16.0
    return "unclear", 10.0


def sector_points(value: float) -> float:
    if pd.isna(value):
        return 0.0
    return 15.0 if value >= 90 else 12.0 if value >= 75 else 8.0 if value >= 50 else 0.0


def relative_strength_points(row: pd.Series) -> float:
    market_values = [row["return_5d_observation_percentile_strength"], row["return_20d_observation_percentile"]]
    if all(pd.notna(value) for value in market_values):
        average = float(np.mean(market_values))
        market_points = 7 if average >= 80 else 5 if average >= 60 else 3 if average >= 50 else 1
    else:
        market_points = 0
    industry = row["return_5d_industry_percentile"]
    industry_points = 0 if pd.isna(industry) else 4 if industry >= 80 else 3 if industry >= 70 else 1
    hit_values = [row["hist_5d_hit_percentile"], row["hist_20d_hit_percentile"]]
    hit_flags = [value >= 50 for value in hit_values if pd.notna(value)]
    hit_points = 2 if len(hit_flags) == 2 and all(hit_flags) else 1 if any(hit_flags) else 0
    atr = row["atr14_observation_percentile"]
    atr_points = 2 if pd.notna(atr) and atr >= 50 else 1 if pd.notna(atr) else 0
    return float(min(15, market_points + industry_points + min(4, hit_points + atr_points)))


def risk_reason(row: pd.Series) -> str:
    labels = {
        "H1_return_5d_hot": "5日涨幅过热",
        "H2_distance_ma20_atr_hot": "偏离MA20/ATR处于高位",
        "H3_ma_gap_atr_hot": "MA5/MA10间距/ATR处于高位",
        "H4_ma5_slope_decline": "MA5斜率从高位连续下降",
        "H5_volume_upper_shadow": "放量滞涨长上影",
    }
    reasons = [labels[name] for name in labels if bool(row[name])]
    if bool(row["price_weakening"]):
        reasons.append("收盘跌破MA5且同步跌破MA10或此前3日低点")
    if row["risk_status"] == "PASS":
        return "未触发观察池过热或价格转弱风险。"
    prefix = "风险否决：" if row["risk_status"] == "VETO" else "风险警告："
    return prefix + "；".join(reasons) + "。"


def selection_reason(row: pd.Series) -> str:
    labels = "、".join(SIGNAL_LABELS[item] for item in str(row["matched_signal"]).split("|") if item)
    sector_text = (f"板块强度百分位{row['sector_rank_percentile']:.2f}" if pd.notna(row["sector_rank_percentile"])
                   else "板块无有效强度评分，未作硬过滤")
    relaxed = "；强势板块领先股采用宽松右侧条件，需人工确认是否属于底部启动" if "leader_relaxed" in str(row["matched_signal"]) else ""
    return (f"匹配{labels}，形态/量价/阶段得分分别为{row['pattern_score']:.2f}/"
            f"{row['volume_price_score']:.2f}/{row['stage_score']:.2f}；{sector_text}，"
            f"观察池5日相对强度百分位{row['return_5d_observation_percentile_strength']:.2f}{relaxed}。")


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    paths = {
        "observation": Path(args.observation), "focus": Path(args.focus), "base": Path(args.base),
        "history": Path(args.history), "indicators": Path(args.indicators), "sectors": Path(args.sectors),
        "v1": Path(args.v1), "market": Path(args.market), "config": Path(args.config),
    }
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"输入不存在: {label}={path}")
    input_hashes_before = {label: sha256(path) for label, path in paths.items()}

    observation = pd.read_csv(paths["observation"], dtype={"ts_code": "string", "code": "string"})
    focus = pd.read_csv(paths["focus"], dtype={"ts_code": "string", "code": "string"})
    base = pd.read_csv(paths["base"], dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
    indicators = pd.read_csv(paths["indicators"], dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
    sectors = pd.read_csv(paths["sectors"], dtype={"trade_date": "string"}, low_memory=False)
    v1 = pd.read_csv(paths["v1"], dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
    market = json.loads(paths["market"].read_text(encoding="utf-8"))
    config = json.loads(paths["config"].read_text(encoding="utf-8"))

    require_columns(observation, ["seq", "code", "ts_code", "name", "is_focus", "as_of_date"], "观察池")
    require_columns(focus, ["seq", "code", "ts_code", "name", "is_focus", "as_of_date"], "重点池")
    require_columns(base, ["ts_code", "trade_date", "name", "industry", "segment", "open", "high", "low", "close", "pct_chg", "vol", "amount"], "基础池")
    indicator_columns = ["ts_code", "trade_date", "history_days", "close", "pct_chg", "open", "high", "low", "vol", "amount", "ma5", "ma10", "ma20", "return_5d_pct", "return_20d_pct", "return_60d_pct", "atr14_pct", "range_20d_pct", "position_60d_pct", "drawdown_from_20d_high_pct", "distance_ma10_pct", "distance_ma20_pct", "ma20_slope_5d_pct", "volume_ratio_5d_20d"]
    v1_columns = ["ts_code", "trade_date", "center_band_ratio_20d_pct", "volume_ratio_1d_prior_10d", "breakout_vs_prior_20d_high_pct", "ma5_slope_3d_pct", "ma5_prev_slope_3d_pct", "ma5_slope_change_pct", "ma5_ma10_gap_pct", "upper_shadow_pct", "hist_5d_target_hit_rate_250d_pct", "hist_20d_target_hit_rate_250d_pct"]
    sector_columns = ["industry", "trade_date", "selection_eligible", "sector_strength_score", "selection_rank"]
    require_columns(indicators, indicator_columns, "指标")
    require_columns(v1, v1_columns, "V1特征")
    require_columns(sectors, sector_columns, "板块指标")
    for label, frame in [("观察池", observation), ("重点池", focus), ("基础池", base), ("指标", indicators), ("V1特征", v1)]:
        ensure_unique(frame, "ts_code", label)
    ensure_unique(sectors, "industry", "板块指标")

    if len(observation) != EXPECTED_OBSERVATION_SIZE or len(focus) != EXPECTED_FOCUS_SIZE:
        raise ValueError(f"股票池数量不符: 观察池{len(observation)}, 重点池{len(focus)}")
    observation_codes = set(observation["ts_code"].dropna().astype(str))
    focus_codes = set(focus["ts_code"].dropna().astype(str))
    if not focus_codes.issubset(observation_codes):
        raise ValueError(f"重点池有{len(focus_codes-observation_codes)}只不属于观察池")
    base_codes = set(base["ts_code"].dropna().astype(str))
    unmatched = sorted(observation_codes - base_codes)
    if unmatched:
        raise ValueError(f"观察池有{len(unmatched)}只未匹配最新行情: {unmatched}")

    for frame in [base, indicators, sectors, v1]:
        frame["trade_date"] = normalize_date(frame["trade_date"])
    dates = {
        "base": set(base["trade_date"].dropna()), "indicators": set(indicators["trade_date"].dropna()),
        "sectors": set(sectors["trade_date"].dropna()), "v1": set(v1["trade_date"].dropna()),
        "market": {str(market.get("trade_date", ""))},
    }
    all_dates = set().union(*dates.values())
    if any(len(values) != 1 for values in dates.values()) or len(all_dates) != 1:
        raise ValueError(f"输入交易日不一致: {dates}")
    trade_date = next(iter(all_dates))
    if market.get("hard_gate") is not False:
        raise ValueError("market hard_gate必须为false")
    excluded = config.get("exclude_from_stock_scoring", [])
    if EXCLUDED_STOCK_FIELD not in excluded:
        raise ValueError(f"配置未排除{EXCLUDED_STOCK_FIELD}")
    if set(excluded) & FORMULA_FIELDS:
        raise ValueError(f"排除字段进入个股公式集合: {sorted(set(excluded)&FORMULA_FIELDS)}")
    weights = config.get("score_weights", {})
    expected = {"pattern": 30, "volume_price": 20, "stage": 20, "sector_sync": 15, "relative_strength_elasticity": 15}
    if any(float(weights.get(key, -1)) != value for key, value in expected.items()) or sum(expected.values()) != 100:
        raise ValueError("V1个股评分权重不符合30/20/20/15/15")

    metrics, future_guard = load_history_metrics(paths["history"], observation_codes, trade_date)
    if set(metrics["ts_code"]) != observation_codes:
        missing_history = sorted(observation_codes - set(metrics["ts_code"]))
        raise ValueError(f"观察池有{len(missing_history)}只没有历史行情: {missing_history}")

    sector_meta = sectors[sector_columns].copy()
    for column in ["selection_eligible", "sector_strength_score", "selection_rank"]:
        sector_meta[column] = pd.to_numeric(sector_meta[column], errors="coerce")
    eligible_sector = sector_meta["selection_eligible"].eq(1) & sector_meta["sector_strength_score"].notna()
    sector_meta["sector_rank_percentile"] = np.nan
    sector_meta.loc[eligible_sector, "sector_rank_percentile"] = percentile(sector_meta.loc[eligible_sector, "sector_strength_score"])
    sector_meta.rename(columns={"selection_rank": "sector_rank"}, inplace=True)

    frame = (observation[["ts_code", "is_focus", "as_of_date"]]
             .merge(base[["ts_code", "name", "industry", "segment"]], on="ts_code", how="left", validate="one_to_one")
             .merge(indicators[indicator_columns].drop(columns="trade_date"), on="ts_code", how="left", validate="one_to_one")
             .merge(v1[v1_columns].drop(columns="trade_date"), on="ts_code", how="left", validate="one_to_one")
             .merge(sector_meta[["industry", "selection_eligible", "sector_strength_score", "sector_rank", "sector_rank_percentile"]], on="industry", how="left", validate="many_to_one")
             .merge(metrics, on="ts_code", how="left", validate="one_to_one"))
    frame.rename(columns={"industry": "sector", "pct_chg": "return_1d_pct"}, inplace=True)
    frame["trade_date"] = trade_date
    frame["market_state"] = str(market.get("market_state", ""))
    frame["market_score"] = pd.to_numeric(market.get("market_score"), errors="coerce")
    frame["market_bottom_status"] = str(market.get("market_bottom_status", ""))
    frame["is_focus"] = pd.to_numeric(frame["is_focus"], errors="coerce").fillna(0).astype(int).astype(bool)
    numeric_columns = [column for column in indicator_columns + v1_columns if column not in {"ts_code", "trade_date"}]
    numeric_columns += ["selection_eligible", "sector_strength_score", "sector_rank", "sector_rank_percentile", "history_days_branch1", "ma5_slope_t_hist_pct", "ma5_slope_t_minus_1_hist_pct", "ma5_slope_t_minus_2_hist_pct", "prior_3d_low"]
    for column in set(numeric_columns):
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame["return_5d_observation_percentile_strength"] = percentile(frame["return_5d_pct"])
    frame["return_20d_observation_percentile"] = percentile(frame["return_20d_pct"])
    frame["return_5d_industry_percentile"] = frame.groupby("sector", dropna=False)["return_5d_pct"].rank(pct=True, method="average") * 100.0
    frame["hist_5d_hit_percentile"] = percentile(frame["hist_5d_target_hit_rate_250d_pct"])
    frame["hist_20d_hit_percentile"] = percentile(frame["hist_20d_target_hit_rate_250d_pct"])
    frame["atr14_observation_percentile"] = percentile(frame["atr14_pct"])
    frame["historical_elasticity_percentile"] = frame[["hist_5d_hit_percentile", "hist_20d_hit_percentile"]].mean(axis=1, skipna=True)

    formula_required = ["range_20d_pct", "center_band_ratio_20d_pct", "ma20_slope_5d_pct", "volume_ratio_1d_prior_10d", "breakout_vs_prior_20d_high_pct", "return_1d_pct", "return_5d_pct", "return_20d_pct", "return_60d_pct", "ma5_slope_3d_pct", "ma5_prev_slope_3d_pct", "ma5_slope_change_pct", "position_60d_pct", "distance_ma10_pct", "ma5_ma10_gap_pct", "distance_ma20_pct", "drawdown_from_20d_high_pct", "volume_ratio_5d_20d", "atr14_pct", "upper_shadow_pct", "close", "high", "ma5", "ma10", "prior_3d_low"]
    frame["serious_data_missing"] = frame[formula_required].isna().any(axis=1) | frame["history_days_branch1"].lt(60) | frame["history_latest_date_branch1"].ne(trade_date)
    frame["st_or_delist"] = frame["name"].astype(str).str.contains(r"ST|退", case=False, regex=True, na=False)
    frame["suspended"] = frame["vol"].fillna(0).le(0) | frame["amount"].fillna(0).le(0)
    main_limit = frame["segment"].isin(["沪市主板", "深市主板"])
    growth_limit = frame["segment"].isin(["创业板", "科创板"])
    at_high = (frame["close"] - frame["high"]).abs().le(np.maximum(0.01, frame["high"].abs() * 1e-6))
    frame["unbuyable_limit_up"] = at_high & ((main_limit & frame["return_1d_pct"].ge(9.8)) | (growth_limit & frame["return_1d_pct"].ge(19.8)))
    frame["direct_excluded"] = frame[["serious_data_missing", "st_or_delist", "suspended", "unbuyable_limit_up"]].any(axis=1)

    signal_result = frame.apply(signal_and_scores, axis=1)
    frame = pd.concat([frame, signal_result], axis=1)
    stages = frame.apply(stage_values, axis=1, result_type="expand")
    stages.columns = ["stage_label", "stage_score"]
    frame = pd.concat([frame, stages], axis=1)
    frame["sector_score"] = frame["sector_rank_percentile"].map(sector_points)
    frame["relative_strength_score"] = frame.apply(relative_strength_points, axis=1)
    frame["total_score"] = frame[["pattern_score", "volume_price_score", "stage_score", "sector_score", "relative_strength_score"]].sum(axis=1).round(2)
    for column in ["pattern_score", "volume_price_score", "stage_score", "sector_score", "relative_strength_score", "total_score"]:
        frame[column] = frame[column].round(2)

    frame["close_vs_ma20_atr"] = safe_ratio(frame["distance_ma20_pct"].clip(lower=0), frame["atr14_pct"])
    frame["ma5_ma10_gap_atr"] = safe_ratio(frame["ma5_ma10_gap_pct"], frame["atr14_pct"])
    frame["return_5d_observation_percentile"] = percentile(frame["return_5d_pct"])
    frame["distance_ma20_atr_observation_percentile"] = percentile(frame["close_vs_ma20_atr"])
    frame["ma_gap_atr_observation_percentile"] = percentile(frame["ma5_ma10_gap_atr"])
    overheat = config.get("overheat", {})
    frame["H1_return_5d_hot"] = frame["return_5d_pct"].ge(float(overheat.get("return_5d_warning_pct", 15))) | frame["return_5d_observation_percentile"].ge(95)
    frame["H2_distance_ma20_atr_hot"] = frame["distance_ma20_atr_observation_percentile"].ge(90)
    frame["H3_ma_gap_atr_hot"] = frame["ma_gap_atr_observation_percentile"].ge(90)
    frame["H4_ma5_slope_decline"] = (frame[["ma5_slope_t_hist_pct", "ma5_slope_t_minus_1_hist_pct", "ma5_slope_t_minus_2_hist_pct"]].gt(0).all(axis=1)
                                             & frame["ma5_slope_t_hist_pct"].lt(frame["ma5_slope_t_minus_1_hist_pct"])
                                             & frame["ma5_slope_t_minus_1_hist_pct"].lt(frame["ma5_slope_t_minus_2_hist_pct"])
                                             & frame["drawdown_from_20d_high_pct"].ge(-3))
    frame["H5_volume_upper_shadow"] = (frame["volume_ratio_1d_prior_10d"].ge(float(overheat.get("high_volume_ratio", 2)))
                                         & frame["return_1d_pct"].le(float(overheat.get("low_gain_warning_pct", 2)))
                                         & frame["upper_shadow_pct"].ge(float(overheat.get("upper_shadow_warning_pct", 40))))
    frame["price_weakening"] = frame["close"].lt(frame["ma5"]) & (frame["close"].lt(frame["ma10"]) | frame["close"].lt(frame["prior_3d_low"]))
    hot_columns = ["H1_return_5d_hot", "H2_distance_ma20_atr_hot", "H3_ma_gap_atr_hot", "H4_ma5_slope_decline", "H5_volume_upper_shadow"]
    frame[hot_columns] = frame[hot_columns].fillna(False).astype(bool)
    frame["overheat_signal_count"] = frame[hot_columns].sum(axis=1).astype(int)
    confirmation = frame["H4_ma5_slope_decline"] | frame["H5_volume_upper_shadow"] | frame["price_weakening"]
    risk_veto = frame["overheat_signal_count"].ge(2) & confirmation
    risk_warning = ~risk_veto & (frame["overheat_signal_count"].ge(1) | frame["price_weakening"])
    frame["risk_status"] = np.select([risk_veto, risk_warning], ["VETO", "WARNING"], default="PASS")
    frame["risk_reasons"] = frame.apply(risk_reason, axis=1)
    frame["fundamental_review_pending"] = True
    frame["selection_reasons"] = frame.apply(selection_reason, axis=1)

    minimum_score = float(weights.get("watch_candidate_min", 60))
    scored = frame.loc[~frame["direct_excluded"] & frame["signal_count"].ge(1) & frame["total_score"].ge(minimum_score)].copy()
    non_veto = scored.loc[~scored["risk_status"].eq("VETO")].copy()
    non_veto["risk_priority"] = non_veto["risk_status"].map({"PASS": 0, "WARNING": 1}).fillna(9)
    non_veto.sort_values(["risk_priority", "total_score", "relative_strength_score", "historical_elasticity_percentile", "ts_code"], ascending=[True, False, False, False, True], na_position="last", kind="stable", inplace=True)
    shortlist = non_veto.head(20).copy()
    shortlist.insert(0, "rank", range(1, len(shortlist) + 1))
    top10 = shortlist.head(10).copy()
    top10["rank"] = range(1, len(top10) + 1)
    shortlist = shortlist[OUTPUT_COLUMNS]
    top10 = top10[OUTPUT_COLUMNS]

    shortlist_path = Path(args.shortlist)
    top10_path = Path(args.top10)
    snapshot_dir = Path(args.snapshot_dir)
    snapshot_path = snapshot_dir / f"branch1_technical_shortlist_{trade_date}.csv"
    diagnostics_path = snapshot_dir / f"branch1_diagnostics_{trade_date}.json"
    backups = backup_existing([shortlist_path, top10_path, snapshot_path, diagnostics_path])
    shortlist_bytes = csv_payload(shortlist)
    top10_bytes = csv_payload(top10)
    atomic_write(shortlist_path, shortlist_bytes)
    atomic_write(snapshot_path, shortlist_bytes)
    atomic_write(top10_path, top10_bytes)

    direct_reason_counts = {
        "serious_data_missing": int(frame["serious_data_missing"].sum()),
        "st_or_delist": int(frame["st_or_delist"].sum()),
        "suspended": int(frame["suspended"].sum()),
        "unbuyable_limit_up": int(frame["unbuyable_limit_up"].sum()),
    }
    vetoed = scored.loc[scored["risk_status"].eq("VETO")]
    veto_reason_counts = Counter()
    for _, row in vetoed.iterrows():
        for column in hot_columns + ["price_weakening"]:
            if bool(row[column]):
                veto_reason_counts[column] += 1
    signal_counts = {signal: int(frame["matched_signal"].str.split("|").map(lambda values: signal in values).sum()) for signal in SIGNAL_LABELS}
    input_hashes_after = {label: sha256(path) for label, path in paths.items()}
    if input_hashes_before != input_hashes_after:
        raise RuntimeError("选择器运行期间输入文件发生变化")
    diagnostics = {
        "branch": "branch1", "run_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trade_date": trade_date, "next_trade_date": args.next_trade_date or None,
        "market_state": market.get("market_state"), "market_score": market.get("market_score"),
        "market_bottom_status": market.get("market_bottom_status"), "market_hard_gate": market.get("hard_gate"),
        "pool_validation": {
            "observation_count": len(observation), "focus_count": len(focus),
            "observation_duplicate_count": int(observation["ts_code"].duplicated().sum()),
            "focus_duplicate_count": int(focus["ts_code"].duplicated().sum()),
            "focus_not_in_observation_count": len(focus_codes - observation_codes),
            "observation_matched_latest_market_count": len(observation_codes & base_codes),
            "observation_unmatched_count": len(unmatched), "unmatched_ts_codes": unmatched,
        },
        "selection": {
            "direct_excluded_unique_count": int(frame["direct_excluded"].sum()),
            "direct_exclusion_reason_counts": direct_reason_counts,
            "signaled_count": int(frame["signal_count"].ge(1).sum()),
            "score_ge_60_count_before_risk": len(scored),
            "risk_veto_count": len(vetoed), "non_veto_score_ge_60_count": len(non_veto),
            "shortlist_count": len(shortlist), "top10_preview_count": len(top10),
            "risk_status_counts_score_ge_60": {key: int(value) for key, value in scored["risk_status"].value_counts().to_dict().items()},
            "risk_veto_reason_counts": dict(veto_reason_counts), "signal_match_counts_all_pool": signal_counts,
        },
        "risk_method": {
            "cross_section": "observation_pool_246_only", "single_overheat_is_warning": True,
            "veto_rule": "at_least_two_overheat_signals_and_slope_decline_or_long_upper_shadow_or_price_weakening",
            "market_state_used_as_hard_gate": False, "focus_identity_affects_score": False,
        },
        "sector_method": {"hard_filter": False, "eligible_sector_count": int(eligible_sector.sum()),
                          "matched_observation_count": int(frame["sector_strength_score"].notna().sum()),
                          "unmatched_or_unscored_observation_count": int(frame["sector_strength_score"].isna().sum())},
        "excluded_from_stock_scoring": list(excluded),
        "excluded_field_used_in_filter_signal_score_or_sort": False,
        "future_data_guard": future_guard,
        "missing_counts_observation_pool": {column: int(value) for column, value in frame.isna().sum().items() if int(value)},
        "input_sha256_before_and_after_identical": True, "input_sha256": input_hashes_after,
        "backups_created": backups, "elapsed_seconds": round(time.perf_counter() - started, 3),
        "outputs": {"shortlist_latest": str(shortlist_path), "shortlist_snapshot": str(snapshot_path),
                    "top10_preview": str(top10_path), "diagnostics": str(diagnostics_path)},
    }
    diagnostics_bytes = (json.dumps(diagnostics, ensure_ascii=False, indent=2, default=json_scalar) + "\n").encode("utf-8")
    atomic_write(diagnostics_path, diagnostics_bytes)
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, default=json_scalar))
    if not shortlist.empty:
        print("\n技术面前20名：")
        print(shortlist[["rank", "ts_code", "name", "is_focus", "sector", "total_score", "relative_strength_score", "matched_signal", "risk_status"]].to_string(index=False))


if __name__ == "__main__":
    main()
