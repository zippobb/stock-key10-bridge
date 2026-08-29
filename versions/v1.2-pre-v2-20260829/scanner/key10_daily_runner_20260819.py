#!/usr/bin/env python3
"""Lightweight D1 review and observation-pool selection for 2026-08-19."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import shutil
import tempfile
import time
import resource
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts


FINAL10 = [
    "605358.SH", "002156.SZ", "600096.SH", "603186.SH", "603650.SH",
    "002272.SZ", "300763.SZ", "002046.SZ", "300065.SZ", "600330.SH",
]
TARGET_DATE = "20260819"
PREVIOUS_DATE = "20260818"
EXCLUDED_STOCK_FIELD = "three_days_no_break_prior_20d_low"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-date", default=TARGET_DATE)
    parser.add_argument("--previous-date", default=PREVIOUS_DATE)
    parser.add_argument("--next-trade-date", required=True)
    parser.add_argument("--observation", default="config/observation_pool.csv")
    parser.add_argument("--focus", default="config/focus_pool.csv")
    parser.add_argument("--base", default="output/base_universe_latest.csv")
    parser.add_argument("--history", default="output/history_daily_latest.csv")
    parser.add_argument("--indicators", default="output/indicators_latest.csv")
    parser.add_argument("--sectors", default="output/sector_indicators_latest.csv")
    parser.add_argument("--v1", default="output/v1_features_latest.csv")
    parser.add_argument("--previous-market", default="output/market_environment_latest.json")
    parser.add_argument("--previous-shortlist", default="data/key10/key10_technical_shortlist_20260818.csv")
    parser.add_argument("--previous-sector-rotation", default="data/sector/sector_rotation_20260818.json")
    parser.add_argument("--previous-diagnostics", default="output/key10_daily_diagnostics_20260818.json")
    parser.add_argument("--config", default="strategy_v1.json")
    parser.add_argument("--branch1", default="branch1_selector.py")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
        if path.is_file():
            backup = path.with_name(f"{path.name}.bak.{stamp}")
            shutil.copy2(path, backup)
            backups.append(str(backup))
    return backups


def csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, lineterminator="\n", float_format="%.6f")
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")


def json_bytes(value: object) -> bytes:
    return (json.dumps(sanitize_json(value), ensure_ascii=False, indent=2, default=json_default, allow_nan=False) + "\n").encode("utf-8")


def sanitize_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(item) for item in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if value is pd.NA:
        return None
    return value


def json_default(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA:
        return None
    raise TypeError(f"not JSON serializable: {type(value)}")


def normalize_date(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace(".0", "", regex=False).str.strip()


def percentile(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").rank(pct=True, method="average") * 100.0


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    return (num / den.where(den.gt(0))).replace([np.inf, -np.inf], np.nan)


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def load_branch1(path: Path):
    spec = importlib.util.spec_from_file_location("branch1_existing", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载branch1_selector.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fetch_daily_once(target_date: str, cache_path: Path) -> tuple[pd.DataFrame, str]:
    required = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    if cache_path.is_file():
        cached = pd.read_csv(cache_path, dtype={"ts_code": "string", "trade_date": "string"})
        cached["trade_date"] = normalize_date(cached["trade_date"])
        require_columns(cached, required, "当日日线缓存")
        if len(cached) >= 5000 and set(cached["trade_date"]) == {target_date} and cached["ts_code"].is_unique:
            return cached, "existing_valid_cache"
        raise ValueError(f"已有当日日线缓存不完整，停止且不覆盖: {cache_path}")
    token = <REDACTED>
    if not token:
        raise RuntimeError("TUSHARE_TOKEN不可用")
    pro = ts.pro_api(token)
    daily = pro.daily(trade_date=target_date)
    if daily is None:
        raise ValueError("数据源没有返回目标日日线")
    daily = daily.copy()
    daily["trade_date"] = normalize_date(daily["trade_date"])
    require_columns(daily, required, "目标日日线")
    if len(daily) < 5000 or set(daily["trade_date"]) != {target_date} or not daily["ts_code"].is_unique:
        raise ValueError(f"目标日日线不完整: rows={len(daily)}, dates={set(daily['trade_date'])}")
    daily.sort_values("ts_code", kind="stable", inplace=True)
    atomic_write(cache_path, csv_bytes(daily))
    return daily, "tushare_single_daily_request"


def fetch_final10_intraday(target_date: str, cache_path: Path) -> tuple[pd.DataFrame, str, list[str]]:
    required = ["ts_code", "trade_time", "open", "high", "low", "close", "vol"]
    if cache_path.is_file():
        bars = pd.read_csv(cache_path, dtype={"ts_code": "string", "trade_time": "string"})
        require_columns(bars, required, "10只5分钟缓存")
        counts = bars.groupby("ts_code").size()
        unavailable = sorted(code for code in FINAL10 if int(counts.get(code, 0)) < 48)
        if set(counts.index).issubset(set(FINAL10)):
            return bars, "existing_cache_with_open_fallback_if_needed", unavailable
        raise ValueError("已有5分钟缓存包含非昨日最终10只，停止且不覆盖")
    token = <REDACTED>
    if not token:
        raise RuntimeError("TUSHARE_TOKEN不可用")
    ts.set_token(token)
    pieces: list[pd.DataFrame] = []
    request_error: str | None = None
    for ts_code in FINAL10:
        try:
            bars = ts.pro_bar(
                ts_code=ts_code, asset="E", freq="5min",
                start_date=f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]} 09:00:00",
                end_date=f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]} 16:00:00",
            )
        except Exception as exc:
            request_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            break
        if bars is None or len(bars) < 48:
            continue
        bars = bars.copy()
        bars["ts_code"] = ts_code
        pieces.append(bars)
        time.sleep(0.15)
    result = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=required + ["amount"])
    require_columns(result, required, "10只5分钟数据")
    if not result.empty:
        result.sort_values(["ts_code", "trade_time"], kind="stable", inplace=True)
    atomic_write(cache_path, csv_bytes(result))
    counts = result.groupby("ts_code").size() if not result.empty else pd.Series(dtype=int)
    unavailable = sorted(code for code in FINAL10 if int(counts.get(code, 0)) < 48)
    source = "tushare_10_codes_5min_only"
    if request_error:
        source += f"; stopped_after_rate_or_api_error={request_error}"
    return result, source, unavailable


def load_recent_market_history(cache_dir: Path, target_daily: pd.DataFrame, target_date: str, base_codes: set[str]) -> tuple[pd.DataFrame, list[str]]:
    dated: list[tuple[str, Path]] = []
    for path in cache_dir.glob("daily_*.csv"):
        date = path.stem.replace("daily_", "")
        if date.isdigit() and date < target_date:
            dated.append((date, path))
    dated.sort()
    selected = dated[-35:]
    if len(selected) < 30:
        raise ValueError(f"轻量市场计算缓存交易日不足: {len(selected)}")
    pieces: list[pd.DataFrame] = []
    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    for date, path in selected:
        frame = pd.read_csv(path, usecols=lambda c: c in needed, dtype={"ts_code": "string", "trade_date": "string"})
        frame["trade_date"] = normalize_date(frame["trade_date"])
        pieces.append(frame.loc[frame["ts_code"].isin(base_codes), needed])
    target = target_daily.loc[target_daily["ts_code"].isin(base_codes), needed].copy()
    pieces.append(target)
    history = pd.concat(pieces, ignore_index=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("轻量市场历史存在ts_code/trade_date重复")
    dates = sorted(history["trade_date"].dropna().unique().tolist())
    return history, dates


def add_light_features(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    numeric = ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    group = frame.groupby("ts_code", sort=False, group_keys=False)
    for window in [5, 10, 20]:
        frame[f"ma{window}"] = group["close"].transform(lambda values, w=window: values.rolling(w, min_periods=w).mean())
    for days in [3, 5, 20]:
        frame[f"return_{days}d_pct"] = (frame["close"] / group["close"].shift(days) - 1.0) * 100.0
    frame["ma20_slope_5d_pct"] = (frame["ma20"] / group["ma20"].shift(5) - 1.0) * 100.0
    frame["vol_ma20"] = group["vol"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["volume_ratio_1d_20d"] = safe_ratio(frame["vol"], frame["vol_ma20"])
    prior20low = group["low"].transform(lambda values: values.shift(1).rolling(20, min_periods=20).min())
    frame["is_new_20d_low"] = frame["low"].lt(prior20low).where(prior20low.notna(), np.nan)
    new_low_as_float = frame["is_new_20d_low"].astype(float)
    frame["new_low_count_3d"] = new_low_as_float.groupby(frame["ts_code"]).transform(lambda values: values.rolling(3, min_periods=3).sum())
    frame["no_new_low_3d"] = frame["new_low_count_3d"].eq(0)
    return frame


def market_day_metrics(day: pd.DataFrame, config: dict[str, object]) -> dict[str, object]:
    valid = {}
    above = day["close"].notna() & day["ma20"].notna()
    bullish = day[["ma5", "ma10", "ma20"]].notna().all(axis=1)
    r5 = day["return_5d_pct"].notna()
    r20 = day["return_20d_pct"].notna()
    slope = day["ma20_slope_5d_pct"].notna()
    values = {
        "above_ma20_ratio_pct": float(day.loc[above, "close"].gt(day.loc[above, "ma20"]).mean() * 100),
        "bullish_alignment_ratio_pct": float((day.loc[bullish, "ma5"].gt(day.loc[bullish, "ma10"]) & day.loc[bullish, "ma10"].gt(day.loc[bullish, "ma20"])).mean() * 100),
        "positive_5d_ratio_pct": float(day.loc[r5, "return_5d_pct"].gt(0).mean() * 100),
        "positive_20d_ratio_pct": float(day.loc[r20, "return_20d_pct"].gt(0).mean() * 100),
        "median_return_5d_pct": float(day.loc[r5, "return_5d_pct"].median()),
        "median_return_20d_pct": float(day.loc[r20, "return_20d_pct"].median()),
        "median_ma20_slope_pct": float(day.loc[slope, "ma20_slope_5d_pct"].median()),
    }
    returns = pd.to_numeric(day["pct_chg"], errors="coerce")
    codes = day["ts_code"].astype(str).str.split(".").str[0]
    wide_limit = codes.str.startswith(("300", "301", "688"))
    limit_threshold = pd.Series(np.where(wide_limit, 19.5, 9.5), index=day.index)
    values.update({
        "advance_count": int(returns.gt(0).sum()),
        "decline_count": int(returns.lt(0).sum()),
        "flat_count": int(returns.eq(0).sum()),
        "limit_up_count": int(returns.ge(limit_threshold).sum()),
        "limit_down_count": int(returns.le(-limit_threshold).sum()),
        "market_median_return_1d_pct": float(returns.median()),
        "total_amount_thousand_yuan": float(pd.to_numeric(day["amount"], errors="coerce").sum()),
    })
    valid.update({
        "above_ma20_ratio_pct": int(above.sum()), "bullish_alignment_ratio_pct": int(bullish.sum()),
        "positive_5d_ratio_pct": int(r5.sum()), "positive_20d_ratio_pct": int(r20.sum()),
        "median_return_5d_pct": int(r5.sum()), "median_return_20d_pct": int(r20.sum()),
        "median_ma20_slope_pct": int(slope.sum()),
    })
    scores = {
        "above_ma20_ratio": values["above_ma20_ratio_pct"],
        "bullish_alignment_ratio": values["bullish_alignment_ratio_pct"],
        "positive_5d_ratio": values["positive_5d_ratio_pct"],
        "positive_20d_ratio": values["positive_20d_ratio_pct"],
        "median_return_5d": float(np.clip(50 + values["median_return_5d_pct"] * 5, 0, 100)),
        "median_return_20d": float(np.clip(50 + values["median_return_20d_pct"] * 2.5, 0, 100)),
        "median_ma20_slope": float(np.clip(50 + values["median_ma20_slope_pct"] * 10, 0, 100)),
    }
    weights = config["market"]["score_weights"]
    score = sum(scores[key] * float(weights[key]) / 100.0 for key in weights)
    result = {"trade_date": str(day["trade_date"].iloc[0]), "universe_size": int(len(day)), **values,
              "market_score_raw": round(score, 2), "valid_sample_counts": valid,
              "score_components": {key: {"score": round(scores[key], 4), "weight": float(weights[key])} for key in weights}}
    return result


def classify_market(score: float, metrics: dict[str, object]) -> str:
    above = float(metrics["above_ma20_ratio_pct"])
    r5 = float(metrics["median_return_5d_pct"])
    r20 = float(metrics["median_return_20d_pct"])
    positive5 = float(metrics["positive_5d_ratio_pct"])
    no_low = float(metrics.get("three_day_no_new_low_ratio_pct", np.nan))
    if score >= 60 and above >= 50 and r20 > 0:
        return "上涨"
    if r20 < 0 and r5 > 0 and positive5 >= 50 and pd.notna(no_low) and no_low >= 55:
        return "下跌中反弹"
    if score < 45 and above < 45 and r20 < 0 and r5 < 0:
        return "下跌"
    return "横盘震荡"


def build_market_environment(light: pd.DataFrame, dates: list[str], config: dict[str, object]) -> dict[str, object]:
    last_dates = dates[-12:]
    raw_records: list[dict[str, object]] = []
    breadth_records: list[dict[str, object]] = []
    for date in last_dates:
        day = light.loc[light["trade_date"].eq(date)].copy()
        raw = market_day_metrics(day, config)
        new_valid = day["new_low_count_3d"].notna()
        current_new_valid = day["is_new_20d_low"].notna() & day["new_low_count_3d"].notna()
        breadth = {
            "trade_date": date,
            "new_20d_low_ratio_pct": round(float(day.loc[current_new_valid, "is_new_20d_low"].mean() * 100), 4),
            "new_20d_low_valid_samples": int(current_new_valid.sum()),
            "three_day_no_new_low_ratio_pct": round(float(day.loc[new_valid, "no_new_low_3d"].mean() * 100), 4),
            "three_day_no_new_low_valid_samples": int(new_valid.sum()),
        }
        raw.update(breadth)
        raw_records.append(raw)
        breadth_records.append(breadth)
    if len(raw_records) < 3:
        raise ValueError("无法计算3日平滑市场分")
    current = raw_records[-1]
    previous_amount = float(raw_records[-2]["total_amount_thousand_yuan"])
    current["previous_total_amount_thousand_yuan"] = previous_amount
    current["amount_change_pct"] = round((float(current["total_amount_thousand_yuan"]) / previous_amount - 1) * 100, 4) if previous_amount > 0 else None
    current["market_score_3d_smoothed"] = round(float(np.mean([record["market_score_raw"] for record in raw_records[-3:]])), 2)
    current["market_score_recent_3d"] = [{"trade_date": record["trade_date"], "market_score_raw": record["market_score_raw"]} for record in raw_records[-3:]]
    current["market_state_raw"] = classify_market(float(current["market_score_raw"]), current)
    current["market_state"] = classify_market(float(current["market_score_3d_smoothed"]), current)

    recent10 = breadth_records[-10:]
    current_new = float(recent10[-1]["new_20d_low_ratio_pct"])
    peak = max(float(record["new_20d_low_ratio_pct"]) for record in recent10)
    contraction = (peak - current_new) / peak * 100 if peak > 0 else np.nan
    current_no = float(recent10[-1]["three_day_no_new_low_ratio_pct"])
    no_low_min = min(float(record["three_day_no_new_low_ratio_pct"]) for record in recent10)
    no_improvement = current_no - no_low_min
    current_day = light.loc[light["trade_date"].eq(current["trade_date"])]
    positive3_valid = current_day["return_3d_pct"].notna()
    positive3 = float(current_day.loc[positive3_valid, "return_3d_pct"].gt(0).mean() * 100)
    median3 = float(current_day.loc[positive3_valid, "return_3d_pct"].median())
    cond_a = peak >= 5 and pd.notna(contraction) and contraction >= 50
    cond_b = current_no >= 95 and no_improvement >= 3
    cond_c = positive3 >= 50 and median3 > 0
    signal_count = int(cond_a) + int(cond_b) + int(cond_c)
    if cond_a and cond_b and cond_c:
        bottom_status = "底部修复已获右侧确认"
    elif current["market_state"] in {"上涨", "横盘震荡"}:
        bottom_status = "非底部判断阶段"
    elif cond_a and cond_b:
        bottom_status = "疑似止跌"
    elif cond_a or cond_b:
        bottom_status = "底部迹象不足"
    else:
        bottom_status = "未见底"
    current.update({
        "market_bottom_status": bottom_status, "market_bottom_signal_count": signal_count,
        "new_low_ratio_peak_10d_pct": round(peak, 4),
        "new_low_ratio_contraction_from_10d_peak_pct": None if pd.isna(contraction) else round(float(contraction), 4),
        "three_day_no_new_low_ratio_10d_low_pct": round(no_low_min, 4),
        "three_day_no_new_low_improvement_pct_point": round(no_improvement, 4),
        "positive_3d_ratio_pct": round(positive3, 4), "median_return_3d_pct": round(median3, 4),
        "bottom_conditions": {"A_new_low_contraction": cond_a, "B_no_new_low_breadth": cond_b, "C_short_term_repair": cond_c},
        "market_bottom_recent_10d": recent10, "hard_gate": False,
    })
    return current


def sector_table_for_date(light: pd.DataFrame, base: pd.DataFrame, date: str) -> pd.DataFrame:
    day = light.loc[light["trade_date"].eq(date)].merge(base[["ts_code", "industry"]], on="ts_code", how="left", validate="one_to_one")
    day = day.loc[day["industry"].notna()].copy()
    grouped = day.groupby("industry", sort=True)
    sector = grouped.agg(
        stock_count=("ts_code", "size"),
        return_1d_mean_pct=("pct_chg", "mean"),
        return_3d_mean_pct=("return_3d_pct", "mean"),
        return_5d_mean_pct=("return_5d_pct", "mean"),
        return_20d_mean_pct=("return_20d_pct", "mean"),
        up_1d_ratio_pct=("pct_chg", lambda values: float(values.gt(0).mean() * 100)),
        total_volume=("vol", "sum"),
        volume_ratio_1d_20d=("volume_ratio_1d_20d", "mean"),
    ).reset_index()
    for days in [1, 3, 5, 20]:
        sector[f"strength_percentile_{days}d"] = percentile(sector[f"return_{days}d_mean_pct"])
        sector[f"sector_rank_{days}d"] = sector[f"return_{days}d_mean_pct"].rank(ascending=False, method="min").astype("Int64")
    sector["up_ratio_percentile"] = percentile(sector["up_1d_ratio_pct"])
    sector["volume_ratio_percentile"] = percentile(sector["volume_ratio_1d_20d"])
    sector["rotation_score"] = (
        sector["strength_percentile_1d"] * 0.25 + sector["strength_percentile_3d"] * 0.25
        + sector["strength_percentile_5d"] * 0.20 + sector["strength_percentile_20d"] * 0.15
        + sector["up_ratio_percentile"] * 0.10 + sector["volume_ratio_percentile"] * 0.05
    ).round(4)
    sector["sector_rotation_rank"] = sector["rotation_score"].rank(ascending=False, method="min").astype("Int64")
    sector["sector_rank_percentile"] = percentile(sector["rotation_score"])
    sector["trade_date"] = date
    return sector


def build_sector_rotation(light: pd.DataFrame, base: pd.DataFrame, previous_date: str, target_date: str) -> tuple[pd.DataFrame, dict[str, object]]:
    previous = sector_table_for_date(light, base, previous_date)
    current = sector_table_for_date(light, base, target_date)
    compare = current.merge(
        previous[["industry", "sector_rotation_rank", "rotation_score", "sector_rank_1d", "sector_rank_3d"]],
        on="industry", how="left", suffixes=("", "_previous"), validate="one_to_one",
    )
    compare["rank_improvement"] = compare["sector_rotation_rank_previous"] - compare["sector_rotation_rank"]
    quartile = max(1, math.ceil(len(compare) * 0.25))
    statuses=[]
    levels=[]
    evidence=[]
    for _, row in compare.iterrows():
        current_rank = int(row["sector_rotation_rank"])
        previous_rank = int(row["sector_rotation_rank_previous"]) if pd.notna(row["sector_rotation_rank_previous"]) else 999
        improvement = previous_rank - current_rank
        if current_rank <= quartile and previous_rank > quartile and improvement >= 5:
            status = "新增强板块"
        elif previous_rank <= quartile and current_rank > quartile:
            status = "转弱板块"
        elif improvement <= -10 and float(row["return_1d_mean_pct"]) < 0:
            status = "转弱板块"
        elif current_rank <= quartile:
            status = "保持强势"
        elif improvement >= 10:
            status = "明显改善"
        else:
            status = "平稳轮动"
        level = "强" if current_rank <= 5 else "较强" if current_rank <= quartile else "中性" if current_rank <= math.ceil(len(compare)*0.6) else "弱"
        statuses.append(status); levels.append(level)
        evidence.append(
            f"综合轮动排名{previous_rank}->{current_rank}，1/3/5/20日行业均值分别"
            f"{row['return_1d_mean_pct']:.2f}%/{row['return_3d_mean_pct']:.2f}%/"
            f"{row['return_5d_mean_pct']:.2f}%/{row['return_20d_mean_pct']:.2f}%，"
            f"上涨占比{row['up_1d_ratio_pct']:.2f}%，量比{row['volume_ratio_1d_20d']:.2f}。"
        )
    compare["sector_rotation_status"] = statuses
    compare["rotation_level"] = levels
    compare["rotation_evidence"] = evidence
    compare.sort_values(["sector_rotation_rank", "industry"], kind="stable", inplace=True)
    top5 = compare.head(5)
    result = {
        "trade_date": target_date, "previous_trade_date": previous_date,
        "sector_count": len(compare), "top_quartile_cutoff_rank": quartile,
        "method": {
            "return_1d_percentile_weight": 25, "return_3d_percentile_weight": 25,
            "return_5d_percentile_weight": 20, "return_20d_percentile_weight": 15,
            "up_ratio_percentile_weight": 10, "volume_ratio_percentile_weight": 5,
        },
        "top5": top5[["sector_rotation_rank", "industry", "rotation_score", "sector_rank_1d", "sector_rank_3d", "return_1d_mean_pct", "return_3d_mean_pct", "return_5d_mean_pct", "return_20d_mean_pct", "up_1d_ratio_pct", "volume_ratio_1d_20d", "sector_rotation_status", "rotation_level", "rotation_evidence"]].to_dict(orient="records"),
        "newly_strong_sectors": compare.loc[compare["sector_rotation_status"].eq("新增强板块"), ["industry", "sector_rotation_rank_previous", "sector_rotation_rank", "rank_improvement", "rotation_evidence"]].to_dict(orient="records"),
        "weakening_sectors": compare.loc[compare["sector_rotation_status"].eq("转弱板块"), ["industry", "sector_rotation_rank_previous", "sector_rotation_rank", "rank_improvement", "rotation_evidence"]].to_dict(orient="records"),
        "all_sectors": compare.to_dict(orient="records"),
    }
    return compare, result


def load_observation_history(history_path: Path, cache_dir: Path, codes: set[str], target_daily: pd.DataFrame, target_date: str) -> tuple[pd.DataFrame, dict[str, object]]:
    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    pieces=[]
    scanned=0
    source_max=""
    future_rows=0
    for chunk in pd.read_csv(history_path, dtype={"ts_code":"string", "trade_date":"string"}, chunksize=150000, low_memory=False):
        require_columns(chunk, needed, "已有历史")
        scanned += len(chunk)
        chunk["trade_date"] = normalize_date(chunk["trade_date"])
        source_max=max(source_max,str(chunk["trade_date"].max()))
        future_rows += int(chunk["trade_date"].ge(target_date).sum())
        selected=chunk.loc[chunk["ts_code"].isin(codes) & chunk["trade_date"].lt(target_date),needed]
        if not selected.empty:
            pieces.append(selected.copy())
    if future_rows:
        raise ValueError(f"原历史包含目标日或未来数据{future_rows}行，停止")
    intermediate_dates: list[str] = []
    intermediate_rows = 0
    for cache_path in sorted(cache_dir.glob("daily_*.csv")):
        date = cache_path.stem.replace("daily_", "")
        if not (date.isdigit() and source_max < date < target_date):
            continue
        cached = pd.read_csv(cache_path, usecols=lambda column: column in needed, dtype={"ts_code":"string", "trade_date":"string"})
        require_columns(cached, needed, f"中间日缓存{date}")
        cached["trade_date"] = normalize_date(cached["trade_date"])
        if set(cached["trade_date"]) != {date} or cached["ts_code"].duplicated().any():
            raise ValueError(f"中间日缓存不完整或重复: {cache_path}")
        selected = cached.loc[cached["ts_code"].isin(codes), needed].copy()
        if len(selected) != len(codes):
            raise ValueError(f"中间日缓存{date}观察池仅匹配{len(selected)}只")
        pieces.append(selected)
        intermediate_dates.append(date)
        intermediate_rows += len(selected)
    today=target_daily.loc[target_daily["ts_code"].isin(codes),needed].copy()
    pieces.append(today)
    history=pd.concat(pieces,ignore_index=True)
    history.sort_values(["ts_code","trade_date"],kind="stable",inplace=True)
    if history.duplicated(["ts_code","trade_date"]).any():
        raise ValueError("观察池历史重复")
    history=history.groupby("ts_code",group_keys=False).tail(300).copy()
    guard={"source_rows_scanned":scanned,"source_max_trade_date":source_max,"target_date":target_date,
           "source_future_or_target_rows":future_rows,"future_rows_used":0,"intermediate_cache_dates":intermediate_dates,
           "intermediate_cache_rows_added":intermediate_rows,"target_snapshot_rows_added":len(today),
           "full_market_history_recomputed":False,"observation_pool_only_full_features":True}
    return history,guard


def observation_features(history: pd.DataFrame, target_date: str) -> pd.DataFrame:
    frame=history.copy()
    for column in ["open","high","low","close","pre_close","pct_chg","vol","amount"]:
        frame[column]=pd.to_numeric(frame[column],errors="coerce")
    frame.sort_values(["ts_code","trade_date"],kind="stable",inplace=True)
    outputs=[]
    for ts_code,group in frame.groupby("ts_code",sort=False):
        g=group.sort_values("trade_date",kind="stable").copy()
        close=g["close"]; high=g["high"]; low=g["low"]; vol=g["vol"]
        ma5=close.rolling(5,min_periods=5).mean(); ma10=close.rolling(10,min_periods=10).mean(); ma20=close.rolling(20,min_periods=20).mean()
        ma60=close.rolling(60,min_periods=60).mean()
        prior_close=close.shift(1)
        tr=pd.concat([(high-low).abs(),(high-prior_close).abs(),(low-prior_close).abs()],axis=1).max(axis=1)
        atr14=tr.rolling(14,min_periods=14).mean()/close*100
        high20=high.rolling(20,min_periods=20).max(); low20=low.rolling(20,min_periods=20).min()
        high60=high.rolling(60,min_periods=60).max(); low60=low.rolling(60,min_periods=60).min()
        return5=(close/close.shift(5)-1)*100; return20=(close/close.shift(20)-1)*100; return60=(close/close.shift(60)-1)*100
        volma5=vol.rolling(5,min_periods=5).mean(); volma20=vol.rolling(20,min_periods=20).mean()
        prior10=vol.shift(1).rolling(10,min_periods=10).mean()
        median20=close.rolling(20,min_periods=20).median()
        center_ratio=pd.Series(np.nan,index=g.index,dtype=float)
        for idx in range(19,len(g)):
            med=median20.iloc[idx]
            if pd.notna(med):
                window=close.iloc[idx-19:idx+1]
                center_ratio.iloc[idx]=float(window.between(med*0.94,med*1.06,inclusive="both").mean()*100)
        prior20high=high.shift(1).rolling(20,min_periods=20).max()
        slope5=(ma5/ma5.shift(3)-1)*100; slope10=(ma10/ma10.shift(3)-1)*100
        hist5=return5.tail(250).dropna(); hist20=return20.tail(250).dropna()
        if g.empty or str(g["trade_date"].iloc[-1]) != target_date:
            continue
        i=-1
        row=g.iloc[i]
        outputs.append({
            "ts_code":ts_code,"trade_date":target_date,"history_days":len(g),"open":row["open"],"high":row["high"],"low":row["low"],"close":row["close"],"pct_chg":row["pct_chg"],"vol":row["vol"],"amount":row["amount"],
            "ma5":ma5.iloc[i],"ma10":ma10.iloc[i],"ma20":ma20.iloc[i],"ma60":ma60.iloc[i],
            "return_5d_pct":return5.iloc[i],"return_20d_pct":return20.iloc[i],"return_60d_pct":return60.iloc[i],"atr14_pct":atr14.iloc[i],
            "range_20d_pct":(high20.iloc[i]/low20.iloc[i]-1)*100 if pd.notna(high20.iloc[i]) and low20.iloc[i]>0 else np.nan,
            "position_60d_pct":(close.iloc[i]-low60.iloc[i])/(high60.iloc[i]-low60.iloc[i])*100 if pd.notna(high60.iloc[i]) and high60.iloc[i]>low60.iloc[i] else np.nan,
            "drawdown_from_20d_high_pct":(close.iloc[i]/high20.iloc[i]-1)*100 if pd.notna(high20.iloc[i]) else np.nan,
            "distance_ma10_pct":(close.iloc[i]/ma10.iloc[i]-1)*100 if pd.notna(ma10.iloc[i]) else np.nan,
            "distance_ma20_pct":(close.iloc[i]/ma20.iloc[i]-1)*100 if pd.notna(ma20.iloc[i]) else np.nan,
            "ma20_slope_5d_pct":(ma20.iloc[i]/ma20.iloc[-6]-1)*100 if len(ma20)>=6 and pd.notna(ma20.iloc[-6]) else np.nan,
            "volume_ratio_5d_20d":volma5.iloc[i]/volma20.iloc[i] if pd.notna(volma20.iloc[i]) and volma20.iloc[i]>0 else np.nan,
            "vol_avg_prior_10d":prior10.iloc[i],"volume_ratio_1d_prior_10d":vol.iloc[i]/prior10.iloc[i] if pd.notna(prior10.iloc[i]) and prior10.iloc[i]>0 else np.nan,
            "close_median_20d":median20.iloc[i],"center_band_ratio_20d_pct":center_ratio.iloc[i],"prior_20d_high":prior20high.iloc[i],
            "breakout_vs_prior_20d_high_pct":(close.iloc[i]/prior20high.iloc[i]-1)*100 if pd.notna(prior20high.iloc[i]) else np.nan,
            "ma5_slope_3d_pct":slope5.iloc[i],"ma5_prev_slope_3d_pct":slope5.iloc[-2] if len(slope5)>=2 else np.nan,
            "ma5_slope_change_pct":slope5.iloc[i]-(slope5.iloc[-2] if len(slope5)>=2 else np.nan),"ma10_slope_3d_pct":slope10.iloc[i],
            "ma5_ma10_gap_pct":abs(ma5.iloc[i]-ma10.iloc[i])/close.iloc[i]*100 if pd.notna(ma5.iloc[i]) and pd.notna(ma10.iloc[i]) and close.iloc[i]>0 else np.nan,
            "upper_shadow_pct":0.0 if row["high"]==row["low"] else (row["high"]-max(row["open"],row["close"]))/(row["high"]-row["low"])*100,
            "hist_5d_target_hit_rate_250d_pct":float(hist5.ge(10).mean()*100) if len(hist5)>=250 else np.nan,
            "hist_20d_target_hit_rate_250d_pct":float(hist20.ge(20).mean()*100) if len(hist20)>=250 else np.nan,
            "max_5d_return_250d_pct":float(hist5.max()) if len(hist5)>=250 else np.nan,"max_20d_return_250d_pct":float(hist20.max()) if len(hist20)>=250 else np.nan,
            "ma5_slope_t_hist_pct":slope5.iloc[i],"ma5_slope_t_minus_1_hist_pct":slope5.iloc[-2] if len(slope5)>=2 else np.nan,"ma5_slope_t_minus_2_hist_pct":slope5.iloc[-3] if len(slope5)>=3 else np.nan,
            "prior_3d_low":low.iloc[-4:-1].min() if len(low)>=4 else np.nan,
        })
    return pd.DataFrame(outputs)


def score_observation(features: pd.DataFrame, observation: pd.DataFrame, base: pd.DataFrame, sector_current: pd.DataFrame, market: dict[str, object], config: dict[str, object], branch1) -> pd.DataFrame:
    sector_meta=sector_current[["industry","rotation_score","sector_rotation_rank","sector_rank_percentile","sector_rank_1d","sector_rank_3d","sector_rotation_status"]].rename(columns={"rotation_score":"sector_strength_score","sector_rotation_rank":"sector_rank"})
    frame=(observation[["ts_code","is_focus"]]
           .merge(base[["ts_code","name","industry","segment"]],on="ts_code",how="left",validate="one_to_one")
           .merge(features,on="ts_code",how="left",validate="one_to_one")
           .merge(sector_meta,on="industry",how="left",validate="many_to_one"))
    frame.rename(columns={"industry":"sector","pct_chg":"return_1d_pct"},inplace=True)
    frame["market_state"]=market["market_state"]; frame["market_score"]=market["market_score_3d_smoothed"]
    frame["market_bottom_status"]=market["market_bottom_status"]
    frame["return_5d_observation_percentile_strength"]=percentile(frame["return_5d_pct"])
    frame["return_20d_observation_percentile"]=percentile(frame["return_20d_pct"])
    frame["return_5d_industry_percentile"]=frame.groupby("sector",dropna=False)["return_5d_pct"].rank(pct=True,method="average")*100
    frame["hist_5d_hit_percentile"]=percentile(frame["hist_5d_target_hit_rate_250d_pct"])
    frame["hist_20d_hit_percentile"]=percentile(frame["hist_20d_target_hit_rate_250d_pct"])
    frame["atr14_observation_percentile"]=percentile(frame["atr14_pct"])
    frame["historical_elasticity_percentile"]=frame[["hist_5d_hit_percentile","hist_20d_hit_percentile"]].mean(axis=1,skipna=True)
    formula_required=["range_20d_pct","center_band_ratio_20d_pct","ma20_slope_5d_pct","volume_ratio_1d_prior_10d","breakout_vs_prior_20d_high_pct","return_1d_pct","return_5d_pct","return_20d_pct","return_60d_pct","ma5_slope_3d_pct","ma5_prev_slope_3d_pct","ma5_slope_change_pct","position_60d_pct","distance_ma10_pct","ma5_ma10_gap_pct","distance_ma20_pct","drawdown_from_20d_high_pct","volume_ratio_5d_20d","atr14_pct","upper_shadow_pct","close","high","ma5","ma10","prior_3d_low"]
    frame["serious_data_missing"]=frame[formula_required].isna().any(axis=1)|frame["history_days"].lt(60)|frame["trade_date"].ne(TARGET_DATE)
    frame["st_or_delist"]=frame["name"].astype(str).str.contains(r"ST|退",case=False,regex=True,na=False)
    frame["suspended"]=frame["vol"].fillna(0).le(0)|frame["amount"].fillna(0).le(0)
    main=frame["segment"].isin(["沪市主板","深市主板"]); growth=frame["segment"].isin(["创业板","科创板"])
    at_high=(frame["close"]-frame["high"]).abs().le(np.maximum(0.01,frame["high"].abs()*1e-6))
    frame["unbuyable_limit_up"]=at_high&((main&frame["return_1d_pct"].ge(9.8))|(growth&frame["return_1d_pct"].ge(19.8)))
    frame["direct_excluded"]=frame[["serious_data_missing","st_or_delist","suspended","unbuyable_limit_up"]].any(axis=1)
    signal=frame.apply(branch1.signal_and_scores,axis=1); frame=pd.concat([frame,signal],axis=1)
    stages=frame.apply(branch1.stage_values,axis=1,result_type="expand"); stages.columns=["stage_label","stage_score"]; frame=pd.concat([frame,stages],axis=1)
    frame["sector_score"]=frame["sector_rank_percentile"].map(branch1.sector_points)
    frame["relative_strength_score"]=frame.apply(branch1.relative_strength_points,axis=1)
    frame["total_score"]=frame[["pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score"]].sum(axis=1).round(2)
    return apply_risk(frame,config)


def apply_risk(frame: pd.DataFrame, config: dict[str, object]) -> pd.DataFrame:
    frame=frame.copy()
    frame["close_vs_ma20_atr"]=safe_ratio(frame["distance_ma20_pct"].clip(lower=0),frame["atr14_pct"])
    frame["ma5_ma10_gap_atr"]=safe_ratio(frame["ma5_ma10_gap_pct"],frame["atr14_pct"])
    frame["return_5d_observation_percentile"]=percentile(frame["return_5d_pct"])
    frame["distance_ma20_atr_observation_percentile"]=percentile(frame["close_vs_ma20_atr"])
    frame["ma_gap_atr_observation_percentile"]=percentile(frame["ma5_ma10_gap_atr"])
    cfg=config.get("overheat",{})
    frame["H1_return_5d_hot"]=frame["return_5d_pct"].ge(float(cfg.get("return_5d_warning_pct",15)))|frame["return_5d_observation_percentile"].ge(95)
    frame["H2_distance_ma20_atr_hot"]=frame["distance_ma20_atr_observation_percentile"].ge(90)
    frame["H3_ma_gap_atr_hot"]=frame["ma_gap_atr_observation_percentile"].ge(90)
    frame["H4_ma5_slope_decline"]=(frame[["ma5_slope_t_hist_pct","ma5_slope_t_minus_1_hist_pct","ma5_slope_t_minus_2_hist_pct"]].gt(0).all(axis=1)&frame["ma5_slope_t_hist_pct"].lt(frame["ma5_slope_t_minus_1_hist_pct"])&frame["ma5_slope_t_minus_1_hist_pct"].lt(frame["ma5_slope_t_minus_2_hist_pct"])&frame["drawdown_from_20d_high_pct"].ge(-3))
    frame["H5_volume_upper_shadow"]=(frame["volume_ratio_1d_prior_10d"].ge(float(cfg.get("high_volume_ratio",2)))&frame["return_1d_pct"].le(float(cfg.get("low_gain_warning_pct",2)))&frame["upper_shadow_pct"].ge(float(cfg.get("upper_shadow_warning_pct",40))))
    frame["price_weakening"]=frame["close"].lt(frame["ma5"])&(frame["close"].lt(frame["ma10"])|frame["close"].lt(frame["prior_3d_low"]))
    hot=["H1_return_5d_hot","H2_distance_ma20_atr_hot","H3_ma_gap_atr_hot","H4_ma5_slope_decline","H5_volume_upper_shadow"]
    frame[hot]=frame[hot].fillna(False).astype(bool); frame["overheat_signal_count"]=frame[hot].sum(axis=1).astype(int)
    confirm=frame["H4_ma5_slope_decline"]|frame["H5_volume_upper_shadow"]|frame["price_weakening"]
    veto=frame["overheat_signal_count"].ge(2)&confirm
    warning=~veto&(frame["overheat_signal_count"].ge(1)|frame["price_weakening"])
    frame["risk_status"]=np.select([veto,warning],["VETO","WARNING"],default="PASS")
    labels={"H1_return_5d_hot":"5日涨幅过热","H2_distance_ma20_atr_hot":"偏离MA20/ATR高位","H3_ma_gap_atr_hot":"MA5/MA10间距/ATR高位","H4_ma5_slope_decline":"MA5斜率连续下降","H5_volume_upper_shadow":"放量滞涨长上影","price_weakening":"价格转弱"}
    def reasons(row):
        items=[text for key,text in labels.items() if bool(row[key])]
        return "未触发风险" if not items else "；".join(items)
    frame["risk_reasons"]=frame.apply(reasons,axis=1)
    return frame


def existing_previous_frame(observation: pd.DataFrame, base: pd.DataFrame, indicators: pd.DataFrame, v1: pd.DataFrame, sectors: pd.DataFrame, history_obs: pd.DataFrame, market_previous: dict[str, object], config: dict[str, object], branch1) -> pd.DataFrame:
    sector=sectors.copy()
    for column in ["selection_eligible","sector_strength_score","selection_rank"]:
        sector[column]=pd.to_numeric(sector[column],errors="coerce")
    eligible=sector["selection_eligible"].eq(1)&sector["sector_strength_score"].notna()
    sector["sector_rank_percentile"]=np.nan
    sector.loc[eligible,"sector_rank_percentile"]=percentile(sector.loc[eligible,"sector_strength_score"])
    sector_meta=sector[["industry","sector_strength_score","selection_rank","sector_rank_percentile"]].rename(columns={"selection_rank":"sector_rank"})
    ind_cols=["ts_code","close","pct_chg","range_20d_pct","ma20_slope_5d_pct","position_60d_pct","return_5d_pct","return_20d_pct","return_60d_pct","distance_ma10_pct","distance_ma20_pct","drawdown_from_20d_high_pct","volume_ratio_5d_20d","atr14_pct","ma5","ma10","high"]
    v1_cols=["ts_code","center_band_ratio_20d_pct","volume_ratio_1d_prior_10d","breakout_vs_prior_20d_high_pct","ma5_slope_3d_pct","ma5_prev_slope_3d_pct","ma5_slope_change_pct","ma5_ma10_gap_pct","upper_shadow_pct","hist_5d_target_hit_rate_250d_pct","hist_20d_target_hit_rate_250d_pct","prior_20d_high"]
    frame=(observation[["ts_code"]].merge(base[["ts_code","name","industry","segment"]],on="ts_code",how="left",validate="one_to_one")
           .merge(indicators[ind_cols],on="ts_code",how="left",validate="one_to_one").merge(v1[v1_cols],on="ts_code",how="left",validate="one_to_one")
           .merge(sector_meta,on="industry",how="left",validate="many_to_one"))
    frame.rename(columns={"industry":"sector","pct_chg":"return_1d_pct"},inplace=True)
    frame["trade_date"]=PREVIOUS_DATE; frame["market_state"]=market_previous.get("market_state"); frame["market_score"]=market_previous.get("market_score"); frame["market_bottom_status"]=market_previous.get("market_bottom_status")
    frame["return_5d_observation_percentile_strength"]=percentile(frame["return_5d_pct"]); frame["return_20d_observation_percentile"]=percentile(frame["return_20d_pct"])
    frame["return_5d_industry_percentile"]=frame.groupby("sector",dropna=False)["return_5d_pct"].rank(pct=True,method="average")*100
    frame["hist_5d_hit_percentile"]=percentile(frame["hist_5d_target_hit_rate_250d_pct"]); frame["hist_20d_hit_percentile"]=percentile(frame["hist_20d_target_hit_rate_250d_pct"]); frame["atr14_observation_percentile"]=percentile(frame["atr14_pct"])
    frame["historical_elasticity_percentile"]=frame[["hist_5d_hit_percentile","hist_20d_hit_percentile"]].mean(axis=1,skipna=True)
    metrics=[]
    prior=history_obs.loc[history_obs["trade_date"].le(PREVIOUS_DATE)].copy()
    for ts_code,g in prior.groupby("ts_code",sort=False):
        g=g.sort_values("trade_date"); close=pd.to_numeric(g["close"],errors="coerce"); low=pd.to_numeric(g["low"],errors="coerce"); ma5=close.rolling(5,min_periods=5).mean(); slopes=(ma5/ma5.shift(3)-1)*100
        metrics.append({"ts_code":ts_code,"ma5_slope_t_hist_pct":slopes.iloc[-1],"ma5_slope_t_minus_1_hist_pct":slopes.iloc[-2],"ma5_slope_t_minus_2_hist_pct":slopes.iloc[-3],"prior_3d_low":low.iloc[-4:-1].min()})
    frame=frame.merge(pd.DataFrame(metrics),on="ts_code",how="left",validate="one_to_one")
    signal=frame.apply(branch1.signal_and_scores,axis=1); frame=pd.concat([frame,signal],axis=1)
    stages=frame.apply(branch1.stage_values,axis=1,result_type="expand"); stages.columns=["stage_label","stage_score"]; frame=pd.concat([frame,stages],axis=1)
    frame["sector_score"]=frame["sector_rank_percentile"].map(branch1.sector_points); frame["relative_strength_score"]=frame.apply(branch1.relative_strength_points,axis=1)
    frame["total_score"]=frame[["pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score"]].sum(axis=1).round(2)
    main=frame["segment"].isin(["沪市主板","深市主板"]); growth=frame["segment"].isin(["创业板","科创板"])
    at_high=(frame["close"]-frame["high"]).abs().le(np.maximum(0.01,frame["high"].abs()*1e-6))
    frame["unbuyable_limit_up"]=at_high&((main&frame["return_1d_pct"].ge(9.8))|(growth&frame["return_1d_pct"].ge(19.8)))
    frame["direct_excluded"]=frame["unbuyable_limit_up"]
    return apply_risk(frame,config)


def signal_diagnostics(row: pd.Series) -> dict[str, object]:
    families={
        "horizontal": [
            ("20日振幅<=18",row["range_20d_pct"]<=18),("中心区占比>=70",row["center_band_ratio_20d_pct"]>=70),("MA20斜率绝对值<=3",abs(row["ma20_slope_5d_pct"])<=3),("量比>=1.5",row["volume_ratio_1d_prior_10d"]>=1.5),("突破偏离>=-2",row["breakout_vs_prior_20d_high_pct"]>=-2),("当日涨幅>=0",row["return_1d_pct"]>=0)],
        "bottom_right_side": [
            ("量比>=1.5",row["volume_ratio_1d_prior_10d"]>=1.5),("5日收益>0",row["return_5d_pct"]>0),("MA5斜率>0",row["ma5_slope_3d_pct"]>0),("60日位置<=60",row["position_60d_pct"]<=60),("行业5日百分位>=70",row["return_5d_industry_percentile"]>=70),("距MA10>=-2",row["distance_ma10_pct"]>=-2)],
        "bottom_relaxed": [
            ("板块百分位>=75",row["sector_rank_percentile"]>=75),("行业5日百分位>=80",row["return_5d_industry_percentile"]>=80),("量比>=1.5",row["volume_ratio_1d_prior_10d"]>=1.5),("5日收益>0",row["return_5d_pct"]>0),("MA5斜率>0",row["ma5_slope_3d_pct"]>0),("60日位置<=75",row["position_60d_pct"]<=75)],
        "contraction": [
            ("量比<=0.8",row["volume_ratio_1d_prior_10d"]<=0.8),("当日涨幅>=-3",row["return_1d_pct"]>=-3),("MA5/MA10间距<=2",row["ma5_ma10_gap_pct"]<=2),("20日收益>0",row["return_20d_pct"]>0),("距MA20>=-2",row["distance_ma20_pct"]>=-2),("距20日高点回撤>=-10",row["drawdown_from_20d_high_pct"]>=-10),("5日量/20日量>=1",row["volume_ratio_5d_20d"]>=1)],
    }
    return {family:{"matched":all(bool(ok) for _,ok in checks),"unmet":[label for label,ok in checks if not bool(ok)]} for family,checks in families.items()}


def bar_vwap(window: pd.DataFrame) -> float:
    vol=pd.to_numeric(window["vol"],errors="coerce")
    if "amount" in window and pd.to_numeric(window["amount"],errors="coerce").notna().any() and vol.sum()>0:
        candidate=float(pd.to_numeric(window["amount"],errors="coerce").sum()*10/vol.sum())
        if float(window["low"].min())*0.98 <= candidate <= float(window["high"].max())*1.02:
            return candidate
    return float((pd.to_numeric(window["close"],errors="coerce")*vol).sum()/vol.sum())


def simulate_buy(ts_code: str, daily_row: pd.Series, bars: pd.DataFrame, prior_risk: str, segment: str) -> dict[str, object]:
    open_price=float(daily_row["open"]); pre_close=float(daily_row["pre_close"]); close_price=float(daily_row["close"])
    gap=(open_price/pre_close-1)*100
    stock=bars.loc[bars["ts_code"].eq(ts_code)].copy()
    stop=-10 if segment in {"创业板","科创板"} else -6
    open_compare=(close_price/open_price-1)*100
    common={"open_price":round(open_price,4),"open_gap_pct":round(gap,4),"open_price_comparison_return_pct":round(open_compare,4),"failure_line_pct":stop}
    if gap > 3:
        return {**common,"simulated_buy_time":None,"simulated_buy_price":None,"buy_price_source":"高开超过3%，不追高/未买入","buy_triggered":False,"close_return_pct":None,"post_buy_max_return_pct":None,"post_buy_max_drawdown_pct":None,"d1_failure_flag":False,"failure_line_status":"未买入，不适用","actual_buyability":"不追高；开盘价仅作比较基准","daily_evaluation":"高开超过3%，按规则未买入；不以事后涨跌替代成交。","intraday_data_mode":"完整5分钟行情" if len(stock)>=48 else "5分钟数据不可取得，开盘价仅作比较基准"}
    if len(stock) < 48 and prior_risk != "WARNING":
        close_return=open_compare
        max_return=(float(daily_row["high"])/open_price-1)*100
        max_drawdown=(float(daily_row["low"])/open_price-1)*100
        failure=max_drawdown<stop
        evaluation=f"PASS且高开不超过3%，以开盘价保守模拟；{'触发回撤线' if failure else 'D1未触发失败线'}。"
        return {**common,"simulated_buy_time":"09:30","simulated_buy_price":round(open_price,4),"buy_price_source":"开盘价","buy_triggered":True,"close_return_pct":round(close_return,4),"post_buy_max_return_pct":round(max_return,4),"post_buy_max_drawdown_pct":round(max_drawdown,4),"d1_failure_flag":failure,"failure_line_status":"触发" if failure else "未触发","actual_buyability":"可按开盘价保守模拟（5分钟数据不可得）","daily_evaluation":evaluation,"intraday_data_mode":"5分钟数据不可取得，开盘价保守基准"}
    if len(stock) < 48 and prior_risk == "WARNING":
        close_return=open_compare
        max_return=(float(daily_row["high"])/open_price-1)*100
        max_drawdown=(float(daily_row["low"])/open_price-1)*100
        return {**common,"simulated_buy_time":None,"simulated_buy_price":round(open_price,4),"buy_price_source":"开盘价比较基准（非标准买点）","buy_triggered":False,"close_return_pct":round(close_return,4),"post_buy_max_return_pct":round(max_return,4),"post_buy_max_drawdown_pct":round(max_drawdown,4),"d1_failure_flag":False,"failure_line_status":"未形成标准买点，不判定","actual_buyability":"5分钟数据不可得，WARNING买点无法确认","daily_evaluation":"仅按开盘价作保守比较，属于非标准买点，不判定实际成交或失败。","intraday_data_mode":"5分钟数据不可取得，开盘价非标准比较基准"}
    stock["trade_time"]=pd.to_datetime(stock["trade_time"]); stock.sort_values("trade_time",inplace=True)
    for column in ["open","high","low","close","vol","amount"]:
        if column in stock: stock[column]=pd.to_numeric(stock[column],errors="coerce")
    need_pullback=prior_risk=="WARNING"
    buy_price=np.nan; buy_time=""; buy_source=""; trigger=False; buy_end=None
    if not need_pullback:
        buy_price=open_price; buy_time="09:30"; buy_source="开盘价"; trigger=True; buy_end=stock["trade_time"].min()
    else:
        for end in range(5,len(stock)):
            window=stock.iloc[end-5:end+1]
            if window["trade_time"].iloc[0].strftime("%H:%M") < "09:45":
                continue
            vwap=bar_vwap(window)
            lows=window["low"].tail(3).tolist()
            no_new_low=lows[1]>=lows[0] and lows[2]>=lows[1]
            previous_close=stock["close"].shift(1).reindex(window.index)
            bar_return=(window["close"]/previous_close-1)*100
            median_vol=float(window["vol"].median())
            obvious_down=((window["close"]<window["open"])&bar_return.le(-1)&window["vol"].gt(median_vol*1.5)).any()
            if vwap<open_price and no_new_low and not obvious_down:
                buy_price=vwap; buy_time=f"{window['trade_time'].iloc[0].strftime('%H:%M')}-{window['trade_time'].iloc[-1].strftime('%H:%M')}"; buy_source="30分钟回踩VWAP"; trigger=True; buy_end=window["trade_time"].iloc[-1]; break
    if trigger:
        after=stock.loc[stock["trade_time"].ge(buy_end)]
        close_return=(close_price/buy_price-1)*100
        max_return=(float(after["high"].max())/buy_price-1)*100
        max_drawdown=(float(after["low"].min())/buy_price-1)*100
        stop=-10 if segment in {"创业板","科创板"} else -6
        failure=max_drawdown<stop
        if failure: evaluation=f"D1触发{stop}%回撤线，需复核"
        elif close_return>=5: evaluation="D1强势"
        elif close_return>=2: evaluation="D1正向"
        elif close_return>-2: evaluation="D1中性"
        else: evaluation="D1偏弱但尚不提前判定失败"
    else:
        close_return=max_return=max_drawdown=np.nan; failure=False; evaluation="未出现合格30分钟回踩，未触发买点"
    return {**common,"simulated_buy_time":buy_time or None,"simulated_buy_price":None if pd.isna(buy_price) else round(float(buy_price),4),"buy_price_source":buy_source or "未触发","buy_triggered":trigger,"close_return_pct":None if pd.isna(close_return) else round(float(close_return),4),"post_buy_max_return_pct":None if pd.isna(max_return) else round(float(max_return),4),"post_buy_max_drawdown_pct":None if pd.isna(max_drawdown) else round(float(max_drawdown),4),"d1_failure_flag":failure,"failure_line_status":"触发" if failure else "未触发" if trigger else "未形成标准买点，不适用","actual_buyability":"已按规则形成标准买点" if trigger else "未出现合格回踩，未买入","daily_evaluation":evaluation,"intraday_data_mode":"完整5分钟行情"}


def build_market_risk_warning(previous_market: dict[str, object], previous_sector: dict[str, object], current_market: dict[str, object]) -> dict[str, object]:
    warnings: list[dict[str, str]] = []
    pos5=float(previous_market.get("positive_5d_ratio_pct", np.nan)); pos20=float(previous_market.get("positive_20d_ratio_pct", np.nan))
    med5=float(previous_market.get("median_return_5d_pct", np.nan)); med20=float(previous_market.get("median_return_20d_pct", np.nan))
    above=float(previous_market.get("above_ma20_ratio_pct", np.nan)); raw=float(previous_market.get("market_score_raw", previous_market.get("market_score", np.nan)))
    divergence=bool(pd.notna(pos5) and pd.notna(pos20) and pos20-pos5>=20)
    expansion=bool(pd.notna(above) and above>=75 and pos20>=80 and med20>=8)
    tech_names={"半导体","元器件","通信设备","IT设备","软件服务","专用机械","机床制造","电气设备"}
    prior_all=previous_sector.get("all_sectors", [])
    retreat=[]
    for row in prior_all:
        if row.get("industry") in tech_names:
            rank1=row.get("sector_rank_1d"); rank3=row.get("sector_rank_3d")
            if rank1 is not None and rank3 is not None and float(rank1)-float(rank3)>=15:
                retreat.append(f"{row['industry']}(1日{int(rank1)}/3日{int(rank3)})")
    tech_retreat=bool(retreat)
    if divergence:
        warnings.append({"warning":"短中期赚钱效应严重背离","evidence":f"截至{PREVIOUS_DATE}，20日正收益比例{pos20:.2f}%而5日仅{pos5:.2f}%，差{pos20-pos5:.2f}个百分点；5/20日收益中位数{med5:.2f}%/{med20:.2f}%。"})
    if expansion:
        warnings.append({"warning":"高位扩张与获利盘集中","evidence":f"截至{PREVIOUS_DATE}，站上MA20比例{above:.2f}%、20日正收益比例{pos20:.2f}%、20日收益中位数{med20:.2f}%，中期获利盘覆盖面较高。"})
    if tech_retreat:
        warnings.append({"warning":"原强科技板块短线退潮","evidence":f"截至{PREVIOUS_DATE}，科技板块1日排名显著弱于3日排名：{'、'.join(retreat[:5])}。"})
    warnings=warnings[:3]
    prior_level="高风险" if len(warnings)>=3 else "警戒" if warnings else "无明显预警"
    current_median=float(current_market.get("market_median_return_1d_pct", np.nan)); adv=int(current_market.get("advance_count",0)); dec=int(current_market.get("decline_count",0))
    current_level="高风险" if pd.notna(current_median) and current_median<=-2 and dec>adv*3 else "警戒" if dec>adv*1.5 else prior_level
    score_masked=bool(pd.notna(raw) and raw>=60 and len(warnings)>=2)
    advice="市场风险不作为绝对开关；20260820降低追高和仓位暴露，PASS优先，等待缩量守线或右侧修复确认，WARNING必须等回踩。" if current_level!="无明显预警" else "市场风险不作为绝对开关；按正式技术与风险规则执行，仍避免高开追价。"
    return {
        "trade_date":TARGET_DATE,"warning_as_of_date":PREVIOUS_DATE,"prior_close_warning_level":prior_level,"current_risk_level":current_level,
        "important_prior_close_warnings":warnings if warnings else [{"warning":"暂无重要预警","evidence":"截至前一交易日未发现达到本任务阈值的重要风险信号。"}],
        "market_score_masked_short_term_fragility":score_masked,
        "score_masking_evidence":f"{PREVIOUS_DATE}原始市场分{raw:.2f}，但同期已有{len(warnings)}项重要脆弱性信号。" if score_masked else "市场分与短期风险未形成明显掩盖矛盾。",
        "external_premarket_data":"未抓取；留待ChatGPT盘前复核，未虚构外盘数据。",
        "next_trade_date": "20260820","execution_advice":advice,
    }


def annotate_sector_rotation(sector_json: dict[str, object]) -> None:
    all_rows=sector_json["all_sectors"]
    tech_names={"半导体","元器件","通信设备","IT设备","软件服务","专用机械","机床制造","电气设备"}
    defensive_names={"种植业","农业综合","渔业","林业","食品","饲料","银行","保险","公路","水务","医药商业"}
    tech=[row for row in all_rows if row.get("industry") in tech_names]
    defensive=[row for row in all_rows if row.get("industry") in defensive_names]
    tech_weak=sum(str(row.get("sector_rotation_status"))=="转弱板块" or int(row.get("sector_rotation_rank",999))>int(row.get("sector_rotation_rank_previous",999))+8 for row in tech)
    defensive_strong=[row for row in defensive if int(row.get("sector_rotation_rank",999))<=20]
    sector_json["important_rotation_changes"]={
        "technology_mainline":"继续退潮" if tech and tech_weak>=max(2,len(tech)//3) else "分化，未形成全面退潮" if tech_weak else "未见明显退潮",
        "technology_evidence":[{"industry":row.get("industry"),"rank_previous":row.get("sector_rotation_rank_previous"),"rank_current":row.get("sector_rotation_rank"),"status":row.get("sector_rotation_status")} for row in tech],
        "defensive_rotation":"延续或增强" if len(defensive_strong)>=2 else "局部防御" if defensive_strong else "未明显延续",
        "defensive_evidence":[{"industry":row.get("industry"),"rank":row.get("sector_rotation_rank"),"status":row.get("sector_rotation_status")} for row in defensive_strong[:5]],
        "first_relative_strength_after_drop":[{"industry":row.get("industry"),"rank":row.get("sector_rotation_rank"),"return_1d_mean_pct":row.get("return_1d_mean_pct"),"up_ratio_pct":row.get("up_1d_ratio_pct")} for row in all_rows[:5]],
    }


def next_day_buyability(row: pd.Series) -> str:
    if str(row["risk_status"]) == "WARNING":
        return "需等待回踩和结构确认；禁止高开超过3%追价"
    if float(row["return_1d_pct"]) >= 8:
        return "PASS但当日涨幅较大；次日只等回踩，不追高"
    return "具备技术观察可买性；次日仍需确认开盘不高于3%及盘口结构"


def build_near_signal_watch(scored: pd.DataFrame, formal_codes: set[str], market_median: float, limit: int = 20) -> pd.DataFrame:
    records=[]
    for _,row in scored.iterrows():
        if row["ts_code"] in formal_codes or bool(row["direct_excluded"]) or str(row["risk_status"])=="VETO":
            continue
        diagnostics=signal_diagnostics(row)
        family,details=min(diagnostics.items(), key=lambda item:(len(item[1]["unmet"]), item[0]))
        if len(details["unmet"]) not in {1,2}:
            continue
        records.append({
            "trade_date":TARGET_DATE,"ts_code":row["ts_code"],"name":row["name"],"is_focus":row["is_focus"],"sector":row["sector"],
            "near_signal_family":family,"unmet_condition_count":len(details["unmet"]),"unmet_conditions":"；".join(details["unmet"]),
            "existing_total_score":row["total_score"],"pattern_score":row["pattern_score"],"volume_price_score":row["volume_price_score"],"stage_score":row["stage_score"],"sector_score":row["sector_score"],"relative_strength_score":row["relative_strength_score"],
            "risk_status":row["risk_status"],"risk_reasons":row["risk_reasons"],"sector_rank":row["sector_rank"],"sector_rank_1d":row["sector_rank_1d"],"sector_rank_3d":row["sector_rank_3d"],"sector_rotation_status":row["sector_rotation_status"],
            "return_1d_pct":row["return_1d_pct"],"relative_market_strength_1d_pct":row["return_1d_pct"]-market_median,
            "note":"仅为近似信号观察层，未进入正式候选，未提高技术分。","fundamental_review_pending":True,
        })
    if not records:
        return pd.DataFrame(columns=["trade_date","ts_code","name","near_signal_family","unmet_conditions"])
    result=pd.DataFrame(records)
    result.sort_values(["unmet_condition_count","risk_status","relative_market_strength_1d_pct","sector_rank","ts_code"],ascending=[True,True,False,True,True],kind="stable",inplace=True)
    result=result.head(limit).copy(); result.insert(0,"watch_rank",range(1,len(result)+1))
    return result


def legacy_main() -> None:
    started=time.perf_counter(); args=parse_args()
    if args.target_date!=TARGET_DATE or args.previous_date!=PREVIOUS_DATE:
        raise ValueError("本程序仅用于指定的20260818轻量任务")
    paths={key:Path(value) for key,value in {"observation":args.observation,"focus":args.focus,"base":args.base,"history":args.history,"indicators":args.indicators,"sectors":args.sectors,"v1":args.v1,"previous_market":args.previous_market,"config":args.config,"branch1":args.branch1}.items()}
    for label,path in paths.items():
        if not path.is_file(): raise FileNotFoundError(f"输入不存在: {label}={path}")
    input_hashes_before={key:sha256(path) for key,path in paths.items()}
    observation=pd.read_csv(paths["observation"],dtype={"ts_code":"string","code":"string"}); focus=pd.read_csv(paths["focus"],dtype={"ts_code":"string","code":"string"})
    if len(observation)!=246 or len(focus)!=83 or observation["ts_code"].duplicated().any() or focus["ts_code"].duplicated().any() or not set(focus["ts_code"]).issubset(set(observation["ts_code"])):
        raise ValueError("更新后的观察池/重点池验证失败")
    if int(pd.to_numeric(observation["is_focus"],errors="coerce").sum())!=83:
        raise ValueError("观察池重点标签不是83只")
    base=pd.read_csv(paths["base"],dtype={"ts_code":"string","trade_date":"string"},low_memory=False)
    indicators=pd.read_csv(paths["indicators"],dtype={"ts_code":"string","trade_date":"string"},low_memory=False)
    sectors=pd.read_csv(paths["sectors"],dtype={"trade_date":"string"},low_memory=False)
    v1=pd.read_csv(paths["v1"],dtype={"ts_code":"string","trade_date":"string"},low_memory=False)
    market_previous=json.loads(paths["previous_market"].read_text(encoding="utf-8")); config=json.loads(paths["config"].read_text(encoding="utf-8")); branch1=load_branch1(paths["branch1"])
    if EXCLUDED_STOCK_FIELD not in config.get("exclude_from_stock_scoring",[]): raise ValueError("排除字段配置缺失")
    if any(set(normalize_date(frame["trade_date"]))!={PREVIOUS_DATE} for frame in [base,indicators,sectors,v1]) or str(market_previous.get("trade_date"))!=PREVIOUS_DATE:
        raise ValueError("昨日输入交易日不一致")
    base_codes=set(base["ts_code"]); observation_codes=set(observation["ts_code"])
    if not observation_codes.issubset(base_codes): raise ValueError("观察池不完全属于基础股票池")

    daily_cache=Path(f"data/history_daily/daily_{TARGET_DATE}.csv")
    intraday_cache=Path(f"data/key10/key10_5min_{TARGET_DATE}.csv")
    daily, daily_source=fetch_daily_once(TARGET_DATE,daily_cache)
    if not observation_codes.issubset(set(daily["ts_code"])):
        missing=sorted(observation_codes-set(daily["ts_code"]))
        raise ValueError(f"目标日观察池行情缺失{len(missing)}只: {missing}")
    intraday,intraday_source,intraday_unavailable=fetch_final10_intraday(TARGET_DATE,intraday_cache)
    recent,market_dates=load_recent_market_history(Path("data/history_daily"),daily,TARGET_DATE,base_codes)
    if market_dates[-1]!=TARGET_DATE or PREVIOUS_DATE not in market_dates: raise ValueError("轻量市场窗口日期异常")
    light=add_light_features(recent)
    market=build_market_environment(light,market_dates,config)
    sector_current,sector_json=build_sector_rotation(light,base,PREVIOUS_DATE,TARGET_DATE)
    sector_json["market_context"]={"market_score_raw":market["market_score_raw"],"market_score_3d_smoothed":market["market_score_3d_smoothed"],"market_state":market["market_state"],"market_bottom_status":market["market_bottom_status"]}

    history_obs,future_guard=load_observation_history(paths["history"],observation_codes,daily,TARGET_DATE)
    features=observation_features(history_obs,TARGET_DATE)
    if len(features)!=246: raise ValueError(f"观察池完整特征仅生成{len(features)}只")
    scored_today=score_observation(features,observation,base,sector_current,market,config,branch1)
    minimum=float(config["score_weights"]["watch_candidate_min"])
    candidates=scored_today.loc[~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&~scored_today["risk_status"].eq("VETO")].copy()
    candidates["risk_priority"]=candidates["risk_status"].map({"PASS":0,"WARNING":1}).fillna(9)
    candidates.sort_values(["risk_priority","total_score","relative_strength_score","historical_elasticity_percentile","ts_code"],ascending=[True,False,False,False,True],na_position="last",kind="stable",inplace=True)
    candidates=candidates.head(20).copy(); candidates.insert(0,"rank",range(1,len(candidates)+1))
    candidates["selection_reasons"]=candidates.apply(branch1.selection_reason,axis=1)
    candidates["fundamental_review_pending"]=True
    shortlist_columns=["rank","trade_date","ts_code","name","is_focus","close","sector","total_score","pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score","matched_signal","primary_signal","risk_status","risk_reasons","market_state","market_score","market_bottom_status","sector_rank_1d","sector_rank_3d","sector_rotation_status","sector_rank","sector_rank_percentile","return_1d_pct","return_5d_pct","return_20d_pct","atr14_pct","close_vs_ma20_atr","ma5_ma10_gap_atr","ma5_slope_3d_pct","ma5_slope_change_pct","upper_shadow_pct","overheat_signal_count","historical_elasticity_percentile","fundamental_review_pending","selection_reasons"]
    shortlist=candidates[shortlist_columns].copy()

    previous_frame=existing_previous_frame(observation,base,indicators,v1,sectors,history_obs,market_previous,config,branch1)
    previous_lookup=previous_frame.set_index("ts_code")
    current_daily=daily.set_index("ts_code")
    sector_lookup=sector_current.set_index("industry")
    review_records=[]
    for ts_code in FINAL10:
        prior=previous_lookup.loc[ts_code]; today=current_daily.loc[ts_code]
        result=simulate_buy(ts_code,today,intraday,str(prior["risk_status"]),str(prior["segment"]))
        sector_name=str(prior["sector"]); current_sector=sector_lookup.loc[sector_name]
        previous_rotation_rank=int(current_sector["sector_rotation_rank_previous"])
        current_rotation_rank=int(current_sector["sector_rotation_rank"])
        review_records.append({"trade_date":TARGET_DATE,"ts_code":ts_code,"name":str(prior["name"]),"segment":str(prior["segment"]),"sector":sector_name,"previous_risk_status":str(prior["risk_status"]),**result,"close_price":float(today["close"]),"sector_rank_previous":previous_rotation_rank,"sector_rank_current":current_rotation_rank,"sector_rank_change":previous_rotation_rank-current_rotation_rank,"sector_rotation_status":str(current_sector["sector_rotation_status"])})
    review=pd.DataFrame(review_records)

    missed_records=[]
    for ts_code in MISSED5:
        prior=previous_lookup.loc[ts_code]; today=current_daily.loc[ts_code]; sector_name=str(prior["sector"]); current_sector=sector_lookup.loc[sector_name]
        sig=signal_diagnostics(prior); open_gap=(float(today["open"])/float(today["pre_close"])-1)*100; open_return=(float(today["close"])/float(today["open"])-1)*100
        main_board=str(prior["segment"]) in {"沪市主板","深市主板"}; limit_threshold=9.8 if main_board else 19.8
        locked=abs(float(today["close"])-float(today["high"]))<=max(0.01,abs(float(today["high"]))*1e-6) and float(today["pct_chg"])>=limit_threshold
        if locked or (open_gap>5 and float(today["low"])>=float(today["open"])*0.995):
            category="D"; category_reason="当日明显高开或快速封板，实际买入可行性差。"
        elif bool(prior["direct_excluded"]):
            category="E"; category_reason="前一日处于明显封板状态，按交易可买性规则合理排除。"
        elif str(prior["risk_status"])=="VETO":
            category="E"; category_reason="前一日风险规则已形成否决，属于风险约束下的合理排除。"
        elif int(prior["signal_count"])>=1:
            category="A"; category_reason="前一日已有完整技术信号，但分数、排序或可买性约束导致遗漏。"
        else:
            min_unmet=min(len(value["unmet"]) for value in sig.values())
            improvement=int(current_sector["rank_improvement"])
            if min_unmet<=2:
                category="B"; category_reason="前一日距离完整信号仅差少量条件，适合进入轮动前兆观察。"
            elif improvement>=10 or str(current_sector["sector_rotation_status"])=="新增强板块":
                category="C"; category_reason="主要来自当日板块轮动加速，前一日完整技术信号不足。"
            else:
                category="B"; category_reason="前一日只有弱前兆，尚不足以进入正式候选。"
        actual="明显难买（快速封板）" if locked else "高开后可买性需盘中确认；按限制未拉取该股5分钟线" if abs(open_gap)>3 else "日线看具备开盘基准可买性；未使用最低价"
        missed_records.append({
            "ts_code":ts_code,"name":str(prior["name"]),"score_date":PREVIOUS_DATE,
            "total_score":float(prior["total_score"]),"pattern_score":float(prior["pattern_score"]),"volume_price_score":float(prior["volume_price_score"]),"stage_score":float(prior["stage_score"]),"sector_score":float(prior["sector_score"]),"relative_strength_score":float(prior["relative_strength_score"]),
            "matched_signal":str(prior["matched_signal"]),"signal_diagnostics":sig,"risk_status":str(prior["risk_status"]),"risk_reasons":str(prior["risk_reasons"]),"direct_excluded_20260817":bool(prior["direct_excluded"]),"direct_exclusion_reason_20260817":"明显无法正常买入的封板" if bool(prior["unbuyable_limit_up"]) else None,
            "sector":sector_name,"sector_rank_20260817_original":None if pd.isna(prior["sector_rank"]) else int(prior["sector_rank"]),"sector_rank_20260817":int(current_sector["sector_rotation_rank_previous"]),"sector_rank_20260818":int(current_sector["sector_rotation_rank"]),"sector_rotation_status":str(current_sector["sector_rotation_status"]),
            "open_20260818":float(today["open"]),"high_20260818":float(today["high"]),"low_20260818":float(today["low"]),"close_20260818":float(today["close"]),"pct_chg_20260818":float(today["pct_chg"]),"open_gap_pct":round(open_gap,4),"open_price_baseline_return_pct":round(open_return,4),
            "standard_pullback_vwap_return_pct":None,"standard_pullback_vwap_note":"按明确限制只拉昨日最终10只的5分钟行情，本股不使用最低价替代。","actual_buyability":actual,"omission_category":category,"omission_category_reason":category_reason,
        })

    review_latest=Path(f"output/key10_review_{TARGET_DATE}.csv"); review_snapshot=Path(f"data/key10/key10_review_{TARGET_DATE}.csv")
    shortlist_latest=Path("output/key10_technical_shortlist_latest.csv"); shortlist_snapshot=Path(f"data/key10/key10_technical_shortlist_{TARGET_DATE}.csv")
    sector_latest=Path("output/sector_rotation_latest.json"); sector_snapshot=Path(f"data/sector/sector_rotation_{TARGET_DATE}.json")
    diagnostics_path=Path(f"output/key10_daily_diagnostics_{TARGET_DATE}.json")
    outputs=[review_latest,review_snapshot,shortlist_latest,shortlist_snapshot,sector_latest,sector_snapshot,diagnostics_path]
    backups=backup_existing(outputs)
    review_payload=csv_bytes(review); shortlist_payload=csv_bytes(shortlist); sector_payload=json_bytes(sector_json)
    atomic_write(review_latest,review_payload); atomic_write(review_snapshot,review_payload)
    atomic_write(shortlist_latest,shortlist_payload); atomic_write(shortlist_snapshot,shortlist_payload)
    atomic_write(sector_latest,sector_payload); atomic_write(sector_snapshot,sector_payload)

    input_hashes_after={key:sha256(path) for key,path in paths.items()}
    intentionally_changed={"observation","focus"}
    unexpected=[key for key in input_hashes_before if key not in intentionally_changed and input_hashes_before[key]!=input_hashes_after[key]]
    if unexpected: raise RuntimeError(f"输入意外变化: {unexpected}")
    diagnostics={
        "run_time":datetime.now().astimezone().isoformat(timespec="seconds"),"target_trade_date":TARGET_DATE,"previous_trade_date":PREVIOUS_DATE,"next_trade_date":args.next_trade_date,
        "pool_validation":{"observation_count":len(observation),"focus_count":len(focus),"observation_duplicates":int(observation["ts_code"].duplicated().sum()),"focus_duplicates":int(focus["ts_code"].duplicated().sum()),"focus_not_observation":len(set(focus["ts_code"])-set(observation["ts_code"]))},
        "data_fetch":{"daily_source":daily_source,"daily_rows":len(daily),"daily_unique_ts_code":int(daily["ts_code"].nunique()),"full_market_daily_requests_this_run":0 if daily_source=="existing_valid_cache" else 1,"intraday_source":intraday_source,"intraday_codes_requested":FINAL10,"intraday_code_count":len(FINAL10),"intraday_unavailable_open_fallback":intraday_unavailable,"missed5_intraday_fetched":False,"market_history_cache_days_read":len(market_dates),"full_historical_api_requests":0},
        "market_environment":market,"sector_rotation_summary":{key:sector_json[key] for key in ["sector_count","top_quartile_cutoff_rank","top5","newly_strong_sectors","weakening_sectors"]},
        "yesterday_final10_review":review.to_dict(orient="records"),"missed_five_analysis":missed_records,
        "today_selection":{"signaled_count":int(scored_today["signal_count"].ge(1).sum()),"score_ge_60_before_risk":int((~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)).sum()),"risk_veto_count":int((~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&scored_today["risk_status"].eq("VETO")).sum()),"shortlist_count":len(shortlist),"risk_status_counts":{str(k):int(v) for k,v in shortlist["risk_status"].value_counts().to_dict().items()},"direct_exclusion_counts":{"serious_data_missing":int(scored_today["serious_data_missing"].sum()),"st_or_delist":int(scored_today["st_or_delist"].sum()),"suspended":int(scored_today["suspended"].sum()),"unbuyable_limit_up":int(scored_today["unbuyable_limit_up"].sum())},"excluded_stock_field":EXCLUDED_STOCK_FIELD,"excluded_field_used_in_stock_formula":False,"focus_affects_score":False,"sector_is_hard_filter":False},
        "future_data_guard":future_guard,"input_sha256":input_hashes_after,"unexpected_input_changes":unexpected,"backups_created":backups,
        "validation":{"review_latest_snapshot_byte_identical":review_payload==review_snapshot.read_bytes(),"shortlist_latest_snapshot_byte_identical":shortlist_payload==shortlist_snapshot.read_bytes(),"sector_latest_snapshot_byte_identical":sector_payload==sector_snapshot.read_bytes(),"shortlist_duplicate_ts_code":int(shortlist["ts_code"].duplicated().sum()),"shortlist_below_60":int(shortlist["total_score"].lt(60).sum()),"shortlist_veto_count":int(shortlist["risk_status"].eq("VETO").sum()),"all_shortlist_from_observation":set(shortlist["ts_code"]).issubset(observation_codes)},
        "elapsed_seconds":round(time.perf_counter()-started,3),"output_files":[str(path) for path in outputs],
    }
    atomic_write(diagnostics_path,json_bytes(diagnostics))
    print(json.dumps({"market":market,"sector_top5":sector_json["top5"],"newly_strong":sector_json["newly_strong_sectors"],"weakening":sector_json["weakening_sectors"],"review_rows":len(review),"shortlist_rows":len(shortlist),"missed5":missed_records,"elapsed_seconds":diagnostics["elapsed_seconds"]},ensure_ascii=False,indent=2,default=json_default))
    print("\nYESTERDAY10")
    print(review.to_string(index=False))
    print("\nTODAY_SHORTLIST")
    print(shortlist[["rank","ts_code","name","is_focus","sector","total_score","pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score","matched_signal","risk_status","sector_rank_1d","sector_rank_3d","sector_rotation_status"]].to_string(index=False))


def read_available_memory_mb() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        return None
    return None


def main() -> None:
    started=time.perf_counter(); memory_before=read_available_memory_mb(); args=parse_args()
    if args.target_date!=TARGET_DATE or args.previous_date!=PREVIOUS_DATE or args.next_trade_date!="20260820":
        raise ValueError("本程序仅用于20260819轻量日更，下一交易日必须为20260820")
    path_values={
        "observation":args.observation,"focus":args.focus,"base":args.base,"history":args.history,
        "indicators":args.indicators,"sectors":args.sectors,"v1":args.v1,"previous_market":args.previous_market,
        "config":args.config,"branch1":args.branch1,"previous_shortlist":args.previous_shortlist,
        "previous_sector_rotation":args.previous_sector_rotation,"previous_diagnostics":args.previous_diagnostics,
    }
    paths={key:Path(value) for key,value in path_values.items()}
    for label,path in paths.items():
        if not path.is_file(): raise FileNotFoundError(f"输入不存在: {label}={path}")
    input_hashes_before={key:sha256(path) for key,path in paths.items()}

    observation=pd.read_csv(paths["observation"],dtype={"ts_code":"string","code":"string"})
    focus=pd.read_csv(paths["focus"],dtype={"ts_code":"string","code":"string"})
    if len(observation)!=246 or len(focus)!=83 or observation["ts_code"].duplicated().any() or focus["ts_code"].duplicated().any() or not set(focus["ts_code"]).issubset(set(observation["ts_code"])):
        raise ValueError("观察池246/重点池83验证失败")
    if int(pd.to_numeric(observation["is_focus"],errors="coerce").sum())!=83:
        raise ValueError("观察池重点标签不是83只")
    base=pd.read_csv(paths["base"],dtype={"ts_code":"string","trade_date":"string"},low_memory=False)
    require_columns(base,["ts_code","name","industry","segment"],"基础股票池")
    if base["ts_code"].duplicated().any(): raise ValueError("基础股票池ts_code重复")
    observation_codes=set(observation["ts_code"]); base_codes=set(base["ts_code"])
    if not observation_codes.issubset(base_codes):
        raise ValueError(f"观察池与基础行情未匹配{len(observation_codes-base_codes)}只")
    config=json.loads(paths["config"].read_text(encoding="utf-8"))
    if EXCLUDED_STOCK_FIELD not in config.get("exclude_from_stock_scoring",[]):
        raise ValueError("three_days_no_break_prior_20d_low排除配置缺失")
    branch1=load_branch1(paths["branch1"])
    previous_shortlist=pd.read_csv(paths["previous_shortlist"],dtype={"ts_code":"string","trade_date":"string"},low_memory=False)
    previous_shortlist["trade_date"]=normalize_date(previous_shortlist["trade_date"])
    if set(previous_shortlist["trade_date"])!={PREVIOUS_DATE} or not set(FINAL10).issubset(set(previous_shortlist["ts_code"])):
        raise ValueError("昨日最终10只无法从20260818技术预选中还原")
    previous_sector=json.loads(paths["previous_sector_rotation"].read_text(encoding="utf-8"))
    previous_diagnostics=json.loads(paths["previous_diagnostics"].read_text(encoding="utf-8"))
    previous_market=previous_diagnostics.get("market_environment",{})
    if str(previous_market.get("trade_date"))!=PREVIOUS_DATE or str(previous_sector.get("trade_date"))!=PREVIOUS_DATE:
        raise ValueError("昨日市场或板块诊断日期不是20260818")

    daily_cache=Path(f"data/history_daily/daily_{TARGET_DATE}.csv")
    intraday_cache=Path(f"data/key10/key10_5min_{TARGET_DATE}.csv")
    daily,daily_source=fetch_daily_once(TARGET_DATE,daily_cache)
    daily_codes=set(daily["ts_code"])
    missing_observation=sorted(observation_codes-daily_codes)
    if missing_observation:
        raise ValueError(f"20260819观察池行情缺失{len(missing_observation)}只: {missing_observation}")
    intraday,intraday_source,intraday_unavailable=fetch_final10_intraday(TARGET_DATE,intraday_cache)

    recent,market_dates=load_recent_market_history(Path("data/history_daily"),daily,TARGET_DATE,base_codes)
    if market_dates[-1]!=TARGET_DATE or PREVIOUS_DATE not in market_dates:
        raise ValueError("轻量市场窗口日期异常")
    light=add_light_features(recent)
    market=build_market_environment(light,market_dates,config)
    policies={"上涨":"可正常筛选，但大跌后避免追高，优先抗跌与率先修复。","横盘震荡":"正常筛选，优先缩量守线、结构未破坏和右侧修复。","下跌中反弹":"优先相对强势板块与领先股，WARNING必须等待确认。","下跌":"不关闭选股，只保留独立走强个股并降低追价意愿。"}
    market["candidate_policy"]=policies[market["market_state"]]
    market["market_summary"]=(f"20260819市场原始分{market['market_score_raw']:.2f}、3日平滑分{market['market_score_3d_smoothed']:.2f}，状态为{market['market_state']}。"
                                      f"当日上涨{market['advance_count']}只、下跌{market['decline_count']}只，收益中位数{market['market_median_return_1d_pct']:.2f}%。")
    sector_current,sector_json=build_sector_rotation(light,base,PREVIOUS_DATE,TARGET_DATE)
    annotate_sector_rotation(sector_json)
    sector_json["market_context"]={"market_score_raw":market["market_score_raw"],"market_score_3d_smoothed":market["market_score_3d_smoothed"],"market_state":market["market_state"],"market_bottom_status":market["market_bottom_status"]}
    risk_warning=build_market_risk_warning(previous_market,previous_sector,market)

    history_obs,future_guard=load_observation_history(paths["history"],Path("data/history_daily"),observation_codes,daily,TARGET_DATE)
    features=observation_features(history_obs,TARGET_DATE)
    if len(features)!=246 or features["ts_code"].duplicated().any():
        raise ValueError(f"观察池完整特征异常: rows={len(features)}, dup={features['ts_code'].duplicated().sum()}")
    scored_today=score_observation(features,observation,base,sector_current,market,config,branch1)
    market_median=float(market["market_median_return_1d_pct"])
    scored_today["relative_market_strength_1d_pct"]=(scored_today["return_1d_pct"]-market_median).round(4)
    scored_today["next_day_buyability"]=scored_today.apply(next_day_buyability,axis=1)
    minimum=float(config["score_weights"]["watch_candidate_min"])
    formal_mask=(~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&~scored_today["risk_status"].eq("VETO"))
    all_formal=scored_today.loc[formal_mask].copy()
    all_formal["risk_priority"]=all_formal["risk_status"].map({"PASS":0,"WARNING":1}).fillna(9)
    all_formal.sort_values(["risk_priority","total_score","relative_strength_score","historical_elasticity_percentile","ts_code"],ascending=[True,False,False,False,True],na_position="last",kind="stable",inplace=True)
    candidates=all_formal.head(20).copy(); candidates.insert(0,"rank",range(1,len(candidates)+1))
    candidates["selection_reasons"]=candidates.apply(branch1.selection_reason,axis=1)
    candidates["fundamental_review_pending"]=True
    shortlist_columns=["rank","trade_date","ts_code","name","is_focus","close","sector","total_score","pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score","matched_signal","primary_signal","risk_status","risk_reasons","market_state","market_score","market_bottom_status","sector_rank","sector_rank_1d","sector_rank_3d","sector_rotation_status","sector_rank_percentile","return_1d_pct","return_5d_pct","return_20d_pct","relative_market_strength_1d_pct","historical_elasticity_percentile","atr14_pct","close_vs_ma20_atr","ma5_ma10_gap_atr","ma5_slope_3d_pct","ma5_slope_change_pct","upper_shadow_pct","overheat_signal_count","next_day_buyability","fundamental_review_pending","selection_reasons"]
    shortlist=candidates[shortlist_columns].copy()
    near_watch=build_near_signal_watch(scored_today,set(shortlist["ts_code"]),market_median) if len(shortlist)<15 else pd.DataFrame()

    prior_lookup=previous_shortlist.set_index("ts_code")
    base_lookup=base.set_index("ts_code")
    current_daily=daily.set_index("ts_code")
    sector_lookup=sector_current.set_index("industry")
    review_records=[]
    for ts_code in FINAL10:
        prior=prior_lookup.loc[ts_code]; base_row=base_lookup.loc[ts_code]; today=current_daily.loc[ts_code]
        prior_risk=str(prior["risk_status"]); segment=str(base_row["segment"]); sector_name=str(prior["sector"])
        result=simulate_buy(ts_code,today,intraday,prior_risk,segment)
        if sector_name not in sector_lookup.index: raise ValueError(f"复盘股票板块无法匹配: {ts_code} {sector_name}")
        current_sector=sector_lookup.loc[sector_name]
        review_records.append({
            "trade_date":TARGET_DATE,"ts_code":ts_code,"name":str(prior["name"]),"is_focus":bool(prior["is_focus"]),"segment":segment,"sector":sector_name,"previous_risk_status":prior_risk,
            **result,"close_price":float(today["close"]),"daily_pct_chg":float(today["pct_chg"]),
            "sector_rank_previous":int(current_sector["sector_rotation_rank_previous"]),"sector_rank_current":int(current_sector["sector_rotation_rank"]),
            "sector_rank_change":int(current_sector["sector_rotation_rank_previous"])-int(current_sector["sector_rotation_rank"]),"sector_rotation_status":str(current_sector["sector_rotation_status"]),
        })
    review=pd.DataFrame(review_records)

    review_latest=Path("output/key10_review_latest.csv"); review_snapshot=Path(f"data/key10/key10_review_{TARGET_DATE}.csv")
    shortlist_latest=Path("output/key10_technical_shortlist_latest.csv"); shortlist_snapshot=Path(f"data/key10/key10_technical_shortlist_{TARGET_DATE}.csv")
    near_latest=Path("output/key10_near_signal_watch_latest.csv"); near_snapshot=Path(f"data/key10/key10_near_signal_watch_{TARGET_DATE}.csv")
    market_latest=Path("output/key10_market_environment_latest.json"); market_snapshot=Path(f"data/market/key10_market_environment_{TARGET_DATE}.json")
    sector_latest=Path("output/sector_rotation_latest.json"); sector_snapshot=Path(f"data/sector/sector_rotation_{TARGET_DATE}.json")
    risk_latest=Path("output/market_risk_warning_latest.json"); risk_snapshot=Path(f"data/market/market_risk_warning_{TARGET_DATE}.json")
    diag_latest=Path("output/key10_daily_diagnostics_latest.json"); diag_snapshot=Path(f"data/key10/key10_daily_diagnostics_{TARGET_DATE}.json")
    outputs=[review_latest,review_snapshot,shortlist_latest,shortlist_snapshot,market_latest,market_snapshot,sector_latest,sector_snapshot,risk_latest,risk_snapshot,diag_latest,diag_snapshot]
    if len(shortlist)<15: outputs.extend([near_latest,near_snapshot])
    backups=backup_existing(outputs)
    review_payload=csv_bytes(review); shortlist_payload=csv_bytes(shortlist); market_payload=json_bytes(market); sector_payload=json_bytes(sector_json); risk_payload=json_bytes(risk_warning)
    atomic_write(review_latest,review_payload); atomic_write(review_snapshot,review_payload)
    atomic_write(shortlist_latest,shortlist_payload); atomic_write(shortlist_snapshot,shortlist_payload)
    atomic_write(market_latest,market_payload); atomic_write(market_snapshot,market_payload)
    atomic_write(sector_latest,sector_payload); atomic_write(sector_snapshot,sector_payload)
    atomic_write(risk_latest,risk_payload); atomic_write(risk_snapshot,risk_payload)
    near_payload=None
    if len(shortlist)<15:
        near_payload=csv_bytes(near_watch); atomic_write(near_latest,near_payload); atomic_write(near_snapshot,near_payload)

    input_hashes_after={key:sha256(path) for key,path in paths.items()}
    unexpected=[key for key in input_hashes_before if input_hashes_before[key]!=input_hashes_after[key]]
    if unexpected: raise RuntimeError(f"输入意外变化: {unexpected}")
    output_hashes={str(path):sha256(path) for path in outputs if path not in {diag_latest,diag_snapshot} and path.is_file()}
    numeric_missing={column:int(pd.to_numeric(shortlist[column],errors="coerce").isna().sum()) for column in ["total_score","pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score","relative_market_strength_1d_pct"]}
    diagnostics={
        "run_time":datetime.now().astimezone().isoformat(timespec="seconds"),"target_trade_date":TARGET_DATE,"previous_trade_date":PREVIOUS_DATE,"next_trade_date":args.next_trade_date,
        "scope_confirmation":{"observation_only_full_features":True,"full_market_single_daily_snapshot_only":True,"full_market_v1_recomputed":False,"full_market_elasticity_recomputed":False,"focus_affects_score":False,"excluded_stock_field":EXCLUDED_STOCK_FIELD,"excluded_field_used_in_stock_formula":False,"formal_rules_modified":False},
        "pool_validation":{"observation_count":len(observation),"focus_count":len(focus),"observation_duplicates":int(observation["ts_code"].duplicated().sum()),"focus_duplicates":int(focus["ts_code"].duplicated().sum()),"focus_not_observation":len(set(focus["ts_code"])-set(observation["ts_code"])),"observation_missing_target_daily":len(missing_observation)},
        "data_fetch":{"daily_source":daily_source,"daily_rows":len(daily),"daily_unique_ts_code":int(daily["ts_code"].nunique()),"full_market_daily_requests_this_run":0 if daily_source=="existing_valid_cache" else 1,"intraday_source":intraday_source,"intraday_codes_allowed":FINAL10,"intraday_other_codes_fetched":False,"intraday_unavailable":intraday_unavailable,"market_cache_dates_read":market_dates,"full_historical_api_requests":0},
        "market_environment":market,"market_risk_warning":risk_warning,"sector_rotation_summary":{key:sector_json[key] for key in ["sector_count","top_quartile_cutoff_rank","top5","newly_strong_sectors","weakening_sectors","important_rotation_changes"]},
        "yesterday_final10_review":review.to_dict(orient="records"),
        "today_selection":{"formal_eligible_count_before_top20":len(all_formal),"formal_output_count":len(shortlist),"near_signal_layer_triggered":len(shortlist)<15,"near_signal_count":len(near_watch),"signaled_count":int(scored_today["signal_count"].ge(1).sum()),"score_ge_60_before_risk":int((~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)).sum()),"risk_veto_count":int((~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&scored_today["risk_status"].eq("VETO")).sum()),"risk_veto_reason_counts":dict(Counter("；".join(scored_today.loc[(~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&scored_today["risk_status"].eq("VETO")),"risk_reasons"].astype(str)).split("；"))) if int((~scored_today["direct_excluded"]&scored_today["signal_count"].ge(1)&scored_today["total_score"].ge(minimum)&scored_today["risk_status"].eq("VETO")).sum()) else {},"risk_status_counts":{str(k):int(v) for k,v in shortlist["risk_status"].value_counts().to_dict().items()},"direct_exclusion_counts":{"serious_data_missing":int(scored_today["serious_data_missing"].sum()),"st_or_delist":int(scored_today["st_or_delist"].sum()),"suspended":int(scored_today["suspended"].sum()),"unbuyable_limit_up":int(scored_today["unbuyable_limit_up"].sum())}},
        "future_data_guard":future_guard,"input_sha256_before":input_hashes_before,"input_sha256_after":input_hashes_after,"unexpected_input_changes":unexpected,"backups_created":backups,"output_sha256_excluding_diagnostics":output_hashes,
        "validation":{"review_rows":len(review),"review_duplicate_ts_code":int(review["ts_code"].duplicated().sum()),"shortlist_duplicate_ts_code":int(shortlist["ts_code"].duplicated().sum()),"shortlist_below_60":int(shortlist["total_score"].lt(60).sum()),"shortlist_veto_count":int(shortlist["risk_status"].eq("VETO").sum()),"all_shortlist_from_observation":set(shortlist["ts_code"]).issubset(observation_codes),"review_latest_snapshot_byte_identical":review_payload==review_snapshot.read_bytes(),"shortlist_latest_snapshot_byte_identical":shortlist_payload==shortlist_snapshot.read_bytes(),"market_latest_snapshot_byte_identical":market_payload==market_snapshot.read_bytes(),"sector_latest_snapshot_byte_identical":sector_payload==sector_snapshot.read_bytes(),"risk_latest_snapshot_byte_identical":risk_payload==risk_snapshot.read_bytes(),"near_latest_snapshot_byte_identical":None if near_payload is None else near_payload==near_snapshot.read_bytes(),"shortlist_missing_counts":numeric_missing,"max_history_date_used":str(history_obs["trade_date"].max()),"future_data_rows_used":int(history_obs["trade_date"].gt(TARGET_DATE).sum())},
        "performance":{"elapsed_seconds_before_diagnostics_write":round(time.perf_counter()-started,3),"memory_available_before_mb":memory_before,"memory_available_after_mb":read_available_memory_mb(),"peak_rss_kb":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)},
        "output_files":[str(path) for path in outputs],
    }
    diag_payload=json_bytes(diagnostics); atomic_write(diag_latest,diag_payload); atomic_write(diag_snapshot,diag_payload)
    print(json.dumps(sanitize_json({"market":market,"market_risk":risk_warning,"sector_top5":sector_json["top5"],"important_rotation":sector_json["important_rotation_changes"],"review_rows":len(review),"formal_count":len(shortlist),"near_count":len(near_watch),"elapsed_seconds":round(time.perf_counter()-started,3)}),ensure_ascii=False,indent=2,allow_nan=False))
    print("\nYESTERDAY10")
    print(review.to_string(index=False))
    print("\nTODAY_FORMAL_SHORTLIST")
    print(shortlist[["rank","ts_code","name","is_focus","sector","total_score","pattern_score","volume_price_score","stage_score","sector_score","relative_strength_score","primary_signal","risk_status","sector_rank_1d","sector_rank_3d","sector_rotation_status","relative_market_strength_1d_pct","next_day_buyability"]].to_string(index=False))
    if len(shortlist)<15:
        print("\nNEAR_SIGNAL_WATCH")
        print(near_watch.to_string(index=False))


if __name__=="__main__":
    main()
