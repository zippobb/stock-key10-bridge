#!/usr/bin/env python3
"""V1.1 lightweight shadow diagnostics for the Stock Key10 pipeline.

This module is deliberately read-only with respect to V1.0 decisions.  It
reuses cached market data, preserves the original candidate records and writes
only the dedicated shadow JSON outputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import resource
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT_DEFAULT = Path("/opt/stock-automation/scanner")
DATE_RE = re.compile(r"^[0-9]{8}$")
TS_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SZ|SH|BJ)$")
HISTORY_COLUMNS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "pct_chg", "vol", "amount",
]


def now_bj() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return round(float(value), 6) if math.isfinite(float(value)) else None
    if pd.isna(value):
        return None
    return value


def coerce_csv(value: str | None) -> object:
    if value is None or value == "" or value.lower() in {"nan", "none", "null"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?[0-9]+", value) and not (len(value) > 1 and value.startswith("0")):
        try:
            return int(value)
        except ValueError:
            pass
    if re.fullmatch(r"-?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        number = finite_number(value)
        return number
    return value


def load_json(path: Path) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"JSON输入不存在或为符号链接: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"JSON不是标准UTF-8: {path.name}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层必须是对象: {path.name}")
    return value


def load_csv_records(path: Path) -> tuple[list[str], list[dict[str, object]]]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"CSV输入不存在或为符号链接: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise RuntimeError(f"CSV无表头: {path.name}")
        rows = [{key: coerce_csv(value) for key, value in row.items()} for row in reader]
        return list(reader.fieldnames), rows


def load_engine(path: Path):
    spec = importlib.util.spec_from_file_location("stock_key10_v11_feature_engine", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载V1.0特征引擎")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_date(series: pd.Series) -> pd.Series:
    return series.astype("string").str.replace("-", "", regex=False).str.replace(r"\.0$", "", regex=True)


def atomic_single(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def build_history_cache(
    history_path: Path,
    observation_path: Path,
    cache_path: Path,
    meta_path: Path,
    codes: set[str],
    history_sha_reference: str,
) -> dict[str, object]:
    started = time.perf_counter()
    awk_script = "FNR==NR { if (FNR>1) wanted[$3]=1; next } FNR==1 || ($1 in wanted)"
    filtered = subprocess.run(
        ["/usr/bin/awk", "-F,", awk_script, str(observation_path), str(history_path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if filtered.returncode != 0:
        error = filtered.stderr.decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"观察池历史缓存构建失败: {error}")
    frame = pd.read_csv(
        io.BytesIO(filtered.stdout),
        usecols=HISTORY_COLUMNS,
        dtype={"ts_code": "string", "trade_date": "string"},
        low_memory=False,
    )
    frame["trade_date"] = normalize_date(frame["trade_date"])
    if set(frame["ts_code"].dropna().astype(str)) != codes:
        missing = sorted(codes - set(frame["ts_code"].dropna().astype(str)))
        raise RuntimeError(f"历史缓存未覆盖完整观察池: {missing[:10]}")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise RuntimeError("历史缓存存在重复ts_code/trade_date")
    counts = frame.groupby("ts_code").size()
    if int(counts.min()) < 60:
        raise RuntimeError(f"历史缓存最短样本仅{int(counts.min())}日")
    frame.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    payload = frame.to_csv(index=False, lineterminator="\n", float_format="%.10g").encode("utf-8")
    atomic_single(cache_path, payload)
    history_stat = history_path.stat()
    meta = {
        "schema_version": "1.1-shadow-history-cache-1",
        "built_at": now_bj().isoformat(timespec="seconds"),
        "observation_pool_sha256": sha256_file(observation_path),
        "observation_pool_count": len(codes),
        "history_source_size": history_stat.st_size,
        "history_source_mtime_ns": history_stat.st_mtime_ns,
        "history_source_sha256_reference": history_sha_reference,
        "cache_sha256": sha256_file(cache_path),
        "cache_rows": len(frame),
        "source_min_trade_date": str(frame["trade_date"].min()),
        "source_max_trade_date": str(frame["trade_date"].max()),
        "build_elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    atomic_single(meta_path, json_bytes(meta))
    return meta


def ensure_history_cache(
    history_path: Path,
    observation_path: Path,
    cache_path: Path,
    meta_path: Path,
    codes: set[str],
    history_sha_reference: str,
    rebuild: bool,
) -> dict[str, object]:
    if rebuild:
        return build_history_cache(history_path, observation_path, cache_path, meta_path, codes, history_sha_reference)
    if not cache_path.is_file() or cache_path.is_symlink() or not meta_path.is_file() or meta_path.is_symlink():
        raise RuntimeError("V1.1观察池历史缓存不存在；需显式执行一次受控缓存构建")
    meta = load_json(meta_path)
    history_stat = history_path.stat()
    checks = {
        "schema": meta.get("schema_version") == "1.1-shadow-history-cache-1",
        "pool_sha": meta.get("observation_pool_sha256") == sha256_file(observation_path),
        "pool_count": int(meta.get("observation_pool_count", -1)) == len(codes),
        "history_size": int(meta.get("history_source_size", -1)) == history_stat.st_size,
        "history_mtime": int(meta.get("history_source_mtime_ns", -1)) == history_stat.st_mtime_ns,
        "history_sha_reference": meta.get("history_source_sha256_reference") == history_sha_reference,
        "cache_sha": meta.get("cache_sha256") == sha256_file(cache_path),
    }
    if not all(checks.values()):
        raise RuntimeError(f"V1.1观察池历史缓存失效，拒绝自动慢速重建: {checks}")
    return meta


def load_observation_history_light(
    history_cache_path: Path,
    history_cache_meta: dict[str, object],
    cache_dir: Path,
    codes: set[str],
    target_date: str,
) -> tuple[pd.DataFrame, dict[str, object], Path]:
    pieces: list[pd.DataFrame] = []
    selected_history = pd.read_csv(
        history_cache_path,
        usecols=HISTORY_COLUMNS,
        dtype={"ts_code": "string", "trade_date": "string"},
        low_memory=False,
    )
    selected_history["trade_date"] = normalize_date(selected_history["trade_date"])
    if not set(selected_history["ts_code"].dropna().astype(str)).issubset(codes):
        raise RuntimeError("精确过滤结果包含观察池外代码")
    source_max = str(history_cache_meta.get("source_max_trade_date", ""))
    if source_max != str(selected_history["trade_date"].max()):
        raise RuntimeError("历史缓存最大交易日与元数据不一致")
    future_or_target_rows = int(selected_history["trade_date"].ge(target_date).sum())
    selected_history = selected_history.loc[selected_history["trade_date"].lt(target_date), HISTORY_COLUMNS]
    if not selected_history.empty:
        pieces.append(selected_history.copy())
    if future_or_target_rows:
        raise RuntimeError(f"原历史包含目标日或未来数据{future_or_target_rows}行")

    cache_dates: list[str] = []
    cache_rows = 0
    for cache_path in sorted(cache_dir.glob("daily_*.csv")):
        date = cache_path.stem.replace("daily_", "")
        if not (DATE_RE.fullmatch(date) and source_max < date <= target_date):
            continue
        cached = pd.read_csv(
            cache_path,
            usecols=HISTORY_COLUMNS,
            dtype={"ts_code": "string", "trade_date": "string"},
            low_memory=False,
        )
        cached["trade_date"] = normalize_date(cached["trade_date"])
        if set(cached["trade_date"].dropna().astype(str)) != {date} or cached["ts_code"].duplicated().any():
            raise RuntimeError(f"日线缓存不完整或重复: {cache_path.name}")
        selected = cached.loc[cached["ts_code"].isin(codes), HISTORY_COLUMNS].copy()
        if len(selected) != len(codes):
            missing = sorted(codes - set(selected["ts_code"].astype(str)))
            raise RuntimeError(f"日线缓存{date}仅匹配{len(selected)}/{len(codes)}只: {missing[:10]}")
        pieces.append(selected)
        cache_dates.append(date)
        cache_rows += len(selected)

    if not cache_dates or cache_dates[-1] != target_date:
        raise RuntimeError(f"缺少目标交易日缓存: {target_date}")
    target_cache = cache_dir / f"daily_{target_date}.csv"
    history = pd.concat(pieces, ignore_index=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise RuntimeError("观察池历史存在重复ts_code/trade_date")
    history = history.groupby("ts_code", group_keys=False).tail(300).copy()
    if int(history["trade_date"].gt(target_date).sum()) != 0:
        raise RuntimeError("影子层检测到未来数据")
    latest_by_code = history.groupby("ts_code", sort=False)["trade_date"].max().astype(str)
    missing_latest = sorted(codes - set(latest_by_code[latest_by_code.eq(target_date)].index.astype(str)))
    if missing_latest:
        raise RuntimeError(f"观察池缺少目标交易日历史: {missing_latest[:10]}")
    guard = {
        "source_rows_scanned": None,
        "source_rows_selected": len(selected_history),
        "history_filter_method": "validated observation-only history cache",
        "source_max_trade_date": source_max,
        "source_future_or_target_rows": future_or_target_rows,
        "cache_dates_loaded": cache_dates,
        "cache_rows_loaded": cache_rows,
        "history_rows_retained": len(history),
        "max_history_date_used": str(history["trade_date"].max()),
        "future_data_rows_used": int(history["trade_date"].gt(target_date).sum()),
        "full_market_history_recomputed": False,
        "observation_pool_only": True,
    }
    return history, guard, target_cache


def condition_result(value: object, condition: dict[str, object]) -> tuple[bool, float, float | None]:
    number = finite_number(value)
    threshold = float(condition["threshold"])
    span = float(condition["soft_span"])
    operator = str(condition["operator"])
    if number is None or span <= 0:
        return False, 0.0, None
    if operator in {"gte", "gt"}:
        met = number >= threshold if operator == "gte" else number > threshold
        normalized = (number - (threshold - span)) / span
        distance = max(threshold - number, 0.0)
    elif operator == "lte":
        met = number <= threshold
        normalized = ((threshold + span) - number) / span
        distance = max(number - threshold, 0.0)
    elif operator == "abs_lte":
        met = abs(number) <= threshold
        normalized = ((threshold + span) - abs(number)) / span
        distance = max(abs(number) - threshold, 0.0)
    else:
        raise RuntimeError(f"未知归一化操作符: {operator}")
    return met, float(np.clip(normalized, 0.0, 1.0)), distance


def signal_completion(metrics: dict[str, object], config: dict[str, object]) -> dict[str, object]:
    families = config["signal_completion"]["families"]
    family_scores: dict[str, float] = {}
    family_unmet: dict[str, list[dict[str, object]]] = {}
    for family, conditions in families.items():
        weighted = 0.0
        weight_sum = 0.0
        unmet: list[dict[str, object]] = []
        for condition in conditions:
            weight = float(condition["weight"])
            actual = metrics.get(str(condition["metric"]))
            met, normalized, distance = condition_result(actual, condition)
            weighted += normalized * weight
            weight_sum += weight
            if not met:
                unmet.append({
                    "label": condition["label"],
                    "metric": condition["metric"],
                    "operator": condition["operator"],
                    "threshold": condition["threshold"],
                    "actual": finite_number(actual),
                    "distance_to_threshold": None if distance is None else round(distance, 6),
                    "condition_completion_pct": round(normalized * 100, 2),
                    "weight": weight,
                })
        family_scores[str(family)] = round(weighted / weight_sum * 100, 2) if weight_sum else 0.0
        family_unmet[str(family)] = unmet
    nearest = max(sorted(family_scores), key=lambda name: family_scores[name])
    return {
        "signal_completion_pct": family_scores[nearest],
        "nearest_signal_family": nearest,
        "unmet_conditions_with_distance": family_unmet[nearest],
        "signal_family_completion_pct": family_scores,
    }


def sector_stage(sector: dict[str, object], config: dict[str, object]) -> str:
    cfg = config["sector_stage"]
    r1 = finite_number(sector.get("return_1d_mean_pct")) or 0.0
    r3 = finite_number(sector.get("return_3d_mean_pct")) or 0.0
    r5 = finite_number(sector.get("return_5d_mean_pct")) or 0.0
    r20 = finite_number(sector.get("return_20d_mean_pct")) or 0.0
    percentile = finite_number(sector.get("sector_rank_percentile")) or 0.0
    up_ratio = finite_number(sector.get("up_1d_ratio_pct")) or 0.0
    volume_ratio = finite_number(sector.get("volume_ratio_1d_20d")) or 0.0
    rank_improvement = finite_number(sector.get("rank_improvement")) or 0.0
    status = str(sector.get("sector_rotation_status", ""))

    crowded = cfg["mature_crowded"]
    if (
        percentile >= float(crowded["min_percentile"])
        and r20 >= float(crowded["min_return_20d_pct"])
        and volume_ratio >= float(crowded["min_volume_ratio"])
    ):
        return "成熟拥挤"
    retreat = cfg["retreat"]
    if status in {"转弱板块", "退出强势"} or rank_improvement <= -float(retreat["rank_drop_min"]) or (
        percentile <= float(retreat["weak_percentile_max"]) and r1 < 0 and r3 < 0
    ):
        return "退潮"
    stable = cfg["stable_trend"]
    if r3 > 0 and r5 > 0 and r20 > 0 and percentile >= float(stable["top_percentile_min"]) and up_ratio >= float(stable["up_ratio_min_pct"]):
        return "稳定趋势"
    right = cfg["right_side"]
    if r1 > 0 and r3 > 0 and r5 >= 0 and rank_improvement >= float(right["rank_improvement_min"]) and up_ratio >= float(right["up_ratio_min_pct"]):
        return "右侧"
    if r1 > 0 and r3 > 0 and (r20 <= 0 or status == "新增强板块"):
        return "底部修复"
    if r3 < 0 and r5 < 0 and r20 < 0:
        return "下跌"
    return "底部修复" if r1 > 0 and up_ratio >= 50 else "下跌"


def setup_type(metrics: dict[str, object], stage: str, config: dict[str, object]) -> str:
    rules = config["setup_rules"]
    r20 = finite_number(metrics.get("return_20d_pct"))
    r5 = finite_number(metrics.get("return_5d_pct"))
    slope = finite_number(metrics.get("ma5_slope_3d_pct"))
    oversold = rules["oversold_repair"]
    if r20 is not None and r5 is not None and slope is not None and r20 <= float(oversold["max_return_20d_pct"]) and r5 > float(oversold["min_return_5d_pct"]) and slope > float(oversold["min_ma5_slope_pct"]):
        return "超跌修复"

    horizontal = rules["horizontal_base"]
    range20 = finite_number(metrics.get("range_20d_pct"))
    center = finite_number(metrics.get("center_band_ratio_20d_pct"))
    ma20_slope = finite_number(metrics.get("ma20_slope_5d_pct"))
    is_horizontal = (
        range20 is not None and center is not None and ma20_slope is not None
        and range20 <= float(horizontal["max_range_20d_pct"])
        and center >= float(horizontal["min_center_band_ratio_pct"])
        and abs(ma20_slope) <= float(horizontal["max_abs_ma20_slope_pct"])
    )
    if is_horizontal:
        upgrade = rules["horizontal_upgrade"]
        volume = finite_number(metrics.get("volume_ratio_1d_prior_10d"))
        breakout = finite_number(metrics.get("breakout_vs_prior_20d_high_pct"))
        r1 = finite_number(metrics.get("return_1d_pct"))
        if (
            volume is not None and slope is not None and breakout is not None and r1 is not None
            and volume >= float(upgrade["min_volume_ratio"])
            and slope > float(upgrade["min_ma5_slope_pct"])
            and breakout >= float(upgrade["min_breakout_pct"])
            and r1 >= float(upgrade["min_return_1d_pct"])
            and stage in set(upgrade["allowed_sector_stages"])
        ):
            return "右侧确认"
        return "横盘待启动"

    right = rules["right_side_confirmation"]
    distance20 = finite_number(metrics.get("distance_ma20_pct"))
    primary = str(metrics.get("primary_signal") or metrics.get("near_signal_family") or "")
    if "bottom" in primary:
        if distance20 is not None and slope is not None and distance20 >= float(right["min_distance_ma20_pct"]) and slope > float(right["min_ma5_slope_pct"]):
            return "右侧确认"
        return "底部反弹"
    if primary == "contraction":
        return "趋势延续" if r20 is not None and r20 > 0 and distance20 is not None and distance20 >= 0 and slope is not None and slope > 0 else "右侧确认"
    return "底部反弹" if r20 is not None and r20 <= 0 else "趋势延续"


def profit_taking_risk(metrics: dict[str, object], setup: str, stage: str, config: dict[str, object]) -> dict[str, object]:
    cfg = config["profit_taking_risk"]
    evidence: list[str] = []
    flags: list[str] = []
    r1 = finite_number(metrics.get("return_1d_pct"))
    r5 = finite_number(metrics.get("return_5d_pct"))
    r20 = finite_number(metrics.get("return_20d_pct"))
    distance_atr = finite_number(metrics.get("close_vs_ma20_atr"))
    gap_atr = finite_number(metrics.get("ma5_ma10_gap_atr"))
    volume = finite_number(metrics.get("volume_ratio_1d_prior_10d"))
    shadow = finite_number(metrics.get("upper_shadow_pct"))
    slope = finite_number(metrics.get("ma5_slope_3d_pct"))
    slope_change = finite_number(metrics.get("ma5_slope_change_pct"))
    volume_5_20 = finite_number(metrics.get("volume_ratio_5d_20d"))
    price_weakening = bool(
        finite_number(metrics.get("close")) is not None
        and finite_number(metrics.get("ma5")) is not None
        and finite_number(metrics.get("ma10")) is not None
        and finite_number(metrics.get("prior_3d_low")) is not None
        and float(metrics["close"]) < float(metrics["ma5"])
        and (float(metrics["close"]) < float(metrics["ma10"]) or float(metrics["close"]) < float(metrics["prior_3d_low"]))
    )
    if setup in {"底部反弹", "超跌修复"} and r5 is not None and slope_change is not None and r5 >= float(cfg["rebound_profit_return_5d_pct"]) and slope_change < 0:
        flags.append("反弹获利回吐"); evidence.append(f"反弹类形态5日涨幅{r5:.2f}%，MA5斜率变化{slope_change:.2f}个百分点。")
    if r5 is not None and r5 >= float(cfg["return_5d_hot_pct"]):
        flags.append("5日涨幅过热"); evidence.append(f"5日涨幅{r5:.2f}%达到过热参考线。")
    if distance_atr is not None and distance_atr >= float(cfg["distance_ma20_atr_high"]):
        flags.append("偏离MA20/ATR过高"); evidence.append(f"相对MA20偏离为{distance_atr:.2f}倍ATR。")
    if gap_atr is not None and gap_atr >= float(cfg["ma5_ma10_gap_atr_high"]):
        flags.append("MA5/MA10间距过大"); evidence.append(f"MA5/MA10间距为{gap_atr:.2f}倍ATR。")
    if volume is not None and r1 is not None and volume >= float(cfg["volume_ratio_high"]) and r1 <= float(cfg["low_gain_pct"]):
        flags.append("放量滞涨"); evidence.append(f"量比{volume:.2f}但当日涨幅仅{r1:.2f}%。")
    if shadow is not None and shadow >= float(cfg["upper_shadow_high_pct"]):
        flags.append("长上影"); evidence.append(f"上影线占振幅{shadow:.2f}%。")
    if ((volume is not None and r1 is not None and volume >= 1.5 and r1 < 0) or (volume_5_20 is not None and slope_change is not None and volume_5_20 >= 1.2 and slope_change < 0)):
        flags.append("量价背离"); evidence.append("成交量扩张但价格或MA5斜率未同步增强。")
    if stage == "成熟拥挤" and r20 is not None and r20 >= float(cfg["high_crowding_return_20d_pct"]):
        flags.append("高位拥挤"); evidence.append(f"板块处于成熟拥挤且个股20日涨幅{r20:.2f}%。")
    if slope_change is not None and slope_change < 0 and "反弹获利回吐" not in flags:
        flags.append("MA5斜率下降确认")
        evidence.append(f"MA5三日斜率较上一期下降{abs(slope_change):.2f}个百分点，作为转弱确认项。")
    confirming = bool((slope_change is not None and slope_change < 0) or "放量滞涨" in flags or "长上影" in flags or price_weakening)
    if len(flags) >= 3 and confirming:
        level = "HIGH"
    elif len(flags) >= 2 or confirming:
        level = "MEDIUM"
    elif flags:
        level = "LOW"
    else:
        healthy = bool(r20 is not None and r20 > 0 and slope is not None and slope > 0 and stage in {"右侧", "稳定趋势"})
        level = "LOW" if healthy else "NONE"
        if healthy:
            evidence.append("趋势和板块持续性尚健康，标记为趋势延续待确认。")
    return {"level": level, "flags": flags, "evidence": evidence, "trend_continuation_pending": "趋势延续待确认" in "".join(evidence)}


def sector_downgrade(metrics: dict[str, object], stage: str, sector: dict[str, object], config: dict[str, object]) -> dict[str, object]:
    cfg = config["sector_downgrade"]
    reasons: list[str] = []
    improvement = finite_number(sector.get("rank_improvement"))
    percentile = finite_number(sector.get("sector_rank_percentile"))
    if stage == "退潮":
        reasons.append("板块阶段为退潮")
    if improvement is not None and improvement <= -float(cfg["rank_drop_min"]):
        reasons.append(f"综合排名较前期下滑{abs(improvement):.0f}位")
    if percentile is not None and percentile <= float(cfg["weak_percentile_max"]):
        reasons.append(f"板块综合强度百分位仅{percentile:.2f}")
    triggered = bool(reasons)
    relative = finite_number(metrics.get("relative_market_strength_1d_pct"))
    if triggered and relative is not None and relative > 0:
        suggestion = "V1.1建议降级，但个股相对强度突出，等待盘中重新确认；不得自动解除VETO。"
    elif triggered:
        suggestion = "V1.1建议降低优先级，等待板块止跌或排名修复；不得自动解除VETO。"
    else:
        suggestion = "未触发板块降级。"
    return {"triggered": triggered, "reasons": reasons, "suggestion": suggestion}


def scenario_type(setup: str, stage: str, metrics: dict[str, object], market: dict[str, object]) -> str:
    if setup in {"底部反弹", "超跌修复"}:
        return "优质超跌修复"
    repair = str(market.get("market_repair_status", ""))
    relative = finite_number(metrics.get("relative_market_strength_1d_pct"))
    volume = finite_number(metrics.get("volume_ratio_1d_prior_10d"))
    if repair in {"无修复", "弱修复"} and stage in {"右侧", "稳定趋势"} and relative is not None and relative > 0 and volume is not None and volume <= 1.2:
        return "防御轮动"
    return "主线启动/右侧"


def atomic_pair(latest: Path, dated: Path, payload: bytes) -> None:
    latest.parent.mkdir(parents=True, exist_ok=True)
    dated.parent.mkdir(parents=True, exist_ok=True)
    staged: dict[Path, Path] = {}
    for path in (latest, dated):
        if path.is_file() and path.read_bytes() == payload:
            os.chmod(path, 0o600)
            continue
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        staged[path] = Path(temp_name)
    try:
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
    finally:
        for temp_path in staged.values():
            if temp_path.exists():
                temp_path.unlink()
    if latest.read_bytes() != dated.read_bytes():
        raise RuntimeError("V1.1影子latest与日期版不一致")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build V1.1 shadow diagnostics without changing V1.0 decisions")
    parser.add_argument("--root", default=str(ROOT_DEFAULT))
    parser.add_argument("--shadow-config")
    parser.add_argument("--latest-path")
    parser.add_argument("--dated-dir")
    parser.add_argument("--history-cache")
    parser.add_argument("--history-cache-meta")
    parser.add_argument("--rebuild-history-cache", action="store_true")
    parser.add_argument("--build-history-cache-only", action="store_true")
    parser.add_argument("--force-recompute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    peak_start_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    root = Path(args.root).resolve(strict=True)
    output = root / "output"
    config_dir = root / "config"
    paths = {
        "technical_shortlist": output / "key10_technical_shortlist_latest.csv",
        "near_signal_candidates": output / "key10_near_signal_watch_latest.csv",
        "market_environment": output / "key10_market_environment_latest.json",
        "market_risk": output / "market_risk_warning_latest.json",
        "sector_rotation": output / "sector_rotation_latest.json",
        "daily_diagnostics": output / "key10_daily_diagnostics_latest.json",
        "observation_pool": config_dir / "observation_pool.csv",
        "focus_pool": config_dir / "focus_pool.csv",
        "base_universe": output / "base_universe_latest.csv",
        "formal_strategy": root / "strategy_v1.json",
        "shadow_strategy": Path(args.shadow_config) if args.shadow_config else config_dir / "strategy_v1_1_shadow.json",
        "feature_engine": root / "stock_key10_engine_v1.py",
        "history": output / "history_daily_latest.csv",
    }
    for path in paths.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"固定输入不存在或为符号链接: {path}")

    shadow_config = load_json(paths["shadow_strategy"])
    formal_config = load_json(paths["formal_strategy"])
    if shadow_config.get("shadow_only") is not True:
        raise RuntimeError("影子配置未标记shadow_only=true")

    _, formal = load_csv_records(paths["technical_shortlist"])
    _, near = load_csv_records(paths["near_signal_candidates"])
    _, observation_rows = load_csv_records(paths["observation_pool"])
    _, focus_rows = load_csv_records(paths["focus_pool"])
    market = load_json(paths["market_environment"])
    market_risk = load_json(paths["market_risk"])
    sectors = load_json(paths["sector_rotation"])
    diagnostics = load_json(paths["daily_diagnostics"])

    observation_codes = [str(row.get("ts_code", "")) for row in observation_rows]
    focus_codes = [str(row.get("ts_code", "")) for row in focus_rows]
    expected_observation = int(shadow_config["expected_observation_pool_count"])
    expected_focus = int(shadow_config["expected_focus_pool_count"])
    if len(observation_codes) != expected_observation or len(set(observation_codes)) != expected_observation:
        raise RuntimeError(f"观察池不是{expected_observation}只唯一ts_code")
    if len(focus_codes) != expected_focus or len(set(focus_codes)) != expected_focus:
        raise RuntimeError(f"重点池不是{expected_focus}只唯一ts_code")
    if not set(focus_codes).issubset(set(observation_codes)):
        raise RuntimeError("重点池不是观察池子集")
    if any(not TS_CODE_RE.fullmatch(code) for code in observation_codes):
        raise RuntimeError("观察池存在非法ts_code")
    focus_tag_mismatch = [
        code for code, row in zip(observation_codes, observation_rows)
        if bool(int(row.get("is_focus") or 0)) != (code in set(focus_codes))
    ]
    if focus_tag_mismatch:
        raise RuntimeError(f"观察池重点标签错配: {focus_tag_mismatch[:10]}")

    history_cache_path = Path(args.history_cache) if args.history_cache else root / "data/v11/observation_history_cache.csv"
    history_cache_meta_path = Path(args.history_cache_meta) if args.history_cache_meta else root / "data/v11/observation_history_cache_meta.json"
    diag_inputs = diagnostics.get("input_sha256_after", {})
    history_sha = diag_inputs.get("history") if isinstance(diag_inputs, dict) else None
    if not isinstance(history_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", history_sha):
        history_sha = sha256_file(paths["history"])
    history_cache_meta = ensure_history_cache(
        paths["history"], paths["observation_pool"], history_cache_path, history_cache_meta_path,
        set(observation_codes), history_sha, args.rebuild_history_cache,
    )
    if args.build_history_cache_only:
        print(json.dumps({
            "status": "history_cache_built",
            "cache": str(history_cache_path),
            "meta": str(history_cache_meta_path),
            "cache_sha256": history_cache_meta["cache_sha256"],
            "rows": history_cache_meta["cache_rows"],
            "elapsed_seconds": history_cache_meta["build_elapsed_seconds"],
        }, ensure_ascii=False, indent=2))
        return 0

    candidate_records: list[tuple[str, dict[str, object]]] = [("formal", row) for row in formal] + [("near_signal", row) for row in near]
    candidate_codes = [str(row.get("ts_code", "")) for _, row in candidate_records]
    if len(set(candidate_codes)) != len(candidate_codes):
        raise RuntimeError("正式候选与近似观察层存在重复或交叉ts_code")
    if not set(candidate_codes).issubset(set(observation_codes)):
        raise RuntimeError(f"候选不属于当前观察池: {sorted(set(candidate_codes)-set(observation_codes))}")
    dates = {str(row.get("trade_date", "")) for _, row in candidate_records}
    if not candidate_records:
        dates = {str(diagnostics.get("target_trade_date", ""))}
    if len(dates) != 1:
        raise RuntimeError(f"候选交易日不一致: {sorted(dates)}")
    trade_date = next(iter(dates))
    if not DATE_RE.fullmatch(trade_date):
        raise RuntimeError(f"无效交易日: {trade_date}")
    for label, value in {
        "market_environment": market.get("trade_date"),
        "market_risk": market_risk.get("trade_date"),
        "sector_rotation": sectors.get("trade_date"),
        "daily_diagnostics": diagnostics.get("target_trade_date"),
    }.items():
        if str(value) != trade_date:
            raise RuntimeError(f"{label}交易日不一致: {value}")

    source_hashes = {
        name: sha256_file(path) for name, path in paths.items()
        if name not in {"history"}
    }
    source_hashes["history"] = history_sha
    source_hashes["observation_history_cache"] = sha256_file(history_cache_path)
    source_hashes["observation_history_cache_meta"] = sha256_file(history_cache_meta_path)
    expected_target_cache = root / "data/history_daily" / f"daily_{trade_date}.csv"
    if not expected_target_cache.is_file() or expected_target_cache.is_symlink():
        raise RuntimeError(f"缺少固定目标日缓存: {expected_target_cache}")
    source_hashes["target_daily_cache"] = sha256_file(expected_target_cache)

    latest_path = Path(args.latest_path) if args.latest_path else output / "key10_v11_shadow_latest.json"
    dated_dir = Path(args.dated_dir) if args.dated_dir else root / "data/v11"
    dated_path = dated_dir / f"key10_v11_shadow_{trade_date}.json"
    if not args.force_recompute and dated_path.is_file() and not dated_path.is_symlink():
        existing = load_json(dated_path)
        if (
            existing.get("schema_version") == shadow_config.get("schema_version")
            and existing.get("trade_date") == trade_date
            and existing.get("source_file_sha256") == source_hashes
            and existing.get("technical_rules_changed") is False
            and existing.get("pool_changed") is False
            and existing.get("future_data_rows") == 0
        ):
            payload = dated_path.read_bytes()
            atomic_pair(latest_path, dated_path, payload)
            print(json.dumps({"status": "success_idempotent", "trade_date": trade_date, "bytes": len(payload), "sha256": sha256_file(latest_path)}, ensure_ascii=False))
            return 0

    history, future_guard, target_cache = load_observation_history_light(
        history_cache_path, history_cache_meta, root / "data/history_daily", set(observation_codes), trade_date
    )
    if target_cache != expected_target_cache:
        raise RuntimeError("目标日缓存路径异常")
    engine = load_engine(paths["feature_engine"])
    candidate_history = history.loc[history["ts_code"].isin(set(candidate_codes))].copy()
    features = engine.observation_features(candidate_history, trade_date)
    if len(features) != len(candidate_codes) or features["ts_code"].duplicated().any():
        raise RuntimeError(f"影子特征异常: rows={len(features)}, duplicates={int(features['ts_code'].duplicated().sum())}")

    base = pd.read_csv(paths["base_universe"], usecols=["ts_code", "name", "industry", "segment"], dtype={"ts_code": "string"}, low_memory=False)
    if base["ts_code"].duplicated().any():
        raise RuntimeError("基础股票池ts_code重复")
    observation = pd.DataFrame(observation_rows)
    observation["ts_code"] = observation["ts_code"].astype("string")
    history["_close_numeric"] = pd.to_numeric(history["close"], errors="coerce")
    history["_return_5d_pct"] = history.groupby("ts_code", sort=False)["_close_numeric"].pct_change(periods=5, fill_method=None) * 100
    pool_return5 = history.groupby("ts_code", sort=False).tail(1)[["ts_code", "_return_5d_pct"]].copy()
    pool_frame = observation[["ts_code", "is_focus"]].merge(
        base, on="ts_code", how="left", validate="one_to_one"
    ).merge(pool_return5, on="ts_code", how="left", validate="one_to_one")
    if len(pool_frame) != expected_observation or pool_frame[["name", "industry", "_return_5d_pct"]].isna().any().any():
        raise RuntimeError("观察池行业5日百分位基础数据异常")
    pool_frame["return_5d_industry_percentile"] = pool_frame.groupby("industry", dropna=False)["_return_5d_pct"].rank(pct=True, method="average") * 100
    candidate_meta = pool_frame.loc[pool_frame["ts_code"].isin(set(candidate_codes)), ["ts_code", "is_focus", "name", "industry", "segment", "return_5d_industry_percentile"]]
    feature_frame = candidate_meta.merge(features, on="ts_code", how="left", validate="one_to_one")
    if feature_frame[["name", "industry", "trade_date"]].isna().any().any():
        raise RuntimeError("观察池基础信息或特征缺失")

    all_sector_rows = sectors.get("all_sectors")
    if not isinstance(all_sector_rows, list):
        raise RuntimeError("板块轮动缺少all_sectors")
    sector_by_industry = {str(row.get("industry")): row for row in all_sector_rows if isinstance(row, dict)}
    feature_frame["sector_rank_percentile"] = feature_frame["industry"].map(
        lambda name: finite_number(sector_by_industry.get(str(name), {}).get("sector_rank_percentile"))
    )
    feature_frame["close_vs_ma20_atr"] = np.where(
        feature_frame["atr14_pct"].gt(0), feature_frame["distance_ma20_pct"].clip(lower=0) / feature_frame["atr14_pct"], np.nan
    )
    feature_frame["ma5_ma10_gap_atr"] = np.where(
        feature_frame["atr14_pct"].gt(0), feature_frame["ma5_ma10_gap_pct"] / feature_frame["atr14_pct"], np.nan
    )
    feature_frame["return_1d_pct"] = pd.to_numeric(feature_frame["pct_chg"], errors="coerce")
    market_median = finite_number(market.get("market_median_return_1d_pct"))
    if market_median is None:
        raise RuntimeError("市场收益中位数缺失")
    feature_frame["relative_market_strength_1d_pct"] = feature_frame["return_1d_pct"] - market_median
    feature_by_code = {str(row["ts_code"]): row.to_dict() for _, row in feature_frame.iterrows()}

    shadow_candidates: list[dict[str, object]] = []
    for layer, original in candidate_records:
        code = str(original["ts_code"])
        metrics = feature_by_code[code]
        metrics["primary_signal"] = original.get("primary_signal")
        metrics["near_signal_family"] = original.get("near_signal_family")
        sector_name = str(metrics["industry"])
        sector_row = sector_by_industry.get(sector_name)
        if not isinstance(sector_row, dict):
            raise RuntimeError(f"候选板块无法匹配: {code} {sector_name}")
        stage = sector_stage(sector_row, shadow_config)
        completion = signal_completion(metrics, shadow_config)
        setup = setup_type(metrics, stage, shadow_config)
        risk = profit_taking_risk(metrics, setup, stage, shadow_config)
        downgrade = sector_downgrade(metrics, stage, sector_row, shadow_config)
        scenario = scenario_type(setup, stage, metrics, market)
        shadow_candidates.append(clean({
            "candidate_layer": layer,
            "v1_0": original,
            "ts_code": code,
            "name": original.get("name") or metrics.get("name"),
            "is_focus": bool(original.get("is_focus")),
            "industry_sector": sector_name,
            "setup_type": setup,
            "sector_stage": stage,
            **completion,
            "profit_taking_risk": risk,
            "sector_downgrade": downgrade,
            "scenario_type": scenario,
            "theme_tags": None,
            "dominant_driver": None,
            "event_risk": None,
            "fundamental_review": None,
            "review_pending": True,
        }))

    scenario_distribution = dict(Counter(str(row["scenario_type"]) for row in shadow_candidates))
    scenario_distribution = {name: int(scenario_distribution.get(name, 0)) for name in ["主线启动/右侧", "优质超跌修复", "防御轮动"]}
    industry_counts = Counter(str(row["industry_sector"]) for row in shadow_candidates)
    threshold = int(shadow_config["concentration_warning_threshold"])
    concentrated = [
        {"industry_sector": name, "candidate_count": count, "warning": f"同一固定行业候选{count}只，超过{threshold}只；仅提示集中度，不强行加入其他标的。"}
        for name, count in sorted(industry_counts.items(), key=lambda item: (-item[1], item[0])) if count > threshold
    ]

    peak_end_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    elapsed = time.perf_counter() - started
    source_daily_pool_count = diagnostics.get("pool_validation", {}).get("observation_count") if isinstance(diagnostics.get("pool_validation"), dict) else None
    result = clean({
        "schema_version": shadow_config["schema_version"],
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "observation_pool_count": len(observation_codes),
        "focus_pool_count": len(focus_codes),
        "source_file_sha256": source_hashes,
        "source_hash_notes": {"history": "取自最近成功日更诊断；若诊断未提供则现场计算", "target_daily_cache": target_cache.name},
        "candidate_counts": {"formal": len(formal), "near_signal": len(near), "total": len(shadow_candidates)},
        "candidates": shadow_candidates,
        "scenario_distribution": scenario_distribution,
        "industry_concentration": {"counts": dict(sorted(industry_counts.items())), "concentration_warning": concentrated},
        "v1_0_invariants": {
            "formal_sha256": source_hashes["technical_shortlist"],
            "near_signal_sha256": source_hashes["near_signal_candidates"],
            "formal_order": [str(row.get("ts_code")) for row in formal],
            "near_signal_order": [str(row.get("ts_code")) for row in near],
            "formal_fields_preserved_under_v1_0": True,
            "near_signal_fields_preserved_under_v1_0": True,
            "score_or_risk_recomputed": False,
            "focus_used_as_label_only": True,
        },
        "technical_rules_changed": False,
        "pool_changed": False,
        "future_data_rows": int(future_guard["future_data_rows_used"]),
        "data_quality": {
            "date_consistent": True,
            "observation_unique": len(set(observation_codes)),
            "focus_unique": len(set(focus_codes)),
            "focus_not_observation": 0,
            "focus_tag_mismatch": 0,
            "candidate_duplicate_or_overlap": 0,
            "candidate_not_observation": 0,
            "candidate_feature_rows": len(features),
            "pool_return_5d_rows": len(pool_return5),
            "feature_missing_codes": 0,
            "source_daily_pool_count": source_daily_pool_count,
            "current_pool_count": len(observation_codes),
            "source_pool_alignment": "current" if source_daily_pool_count == len(observation_codes) else "candidate_membership_verified; source daily result predates current pool size",
            "history_guard": future_guard,
        },
        "performance": {
            "elapsed_seconds": round(elapsed, 3),
            "peak_rss_mb": round(peak_end_kb / 1024, 3),
            "peak_rss_increase_after_import_mb": round(max(0, peak_end_kb - peak_start_kb) / 1024, 3),
        },
    })
    if result["future_data_rows"] != 0:
        raise RuntimeError("future_data_rows非零，拒绝写入")
    payload = json_bytes(result)
    atomic_pair(latest_path, dated_path, payload)
    print(json.dumps({
        "status": "success",
        "trade_date": trade_date,
        "formal": len(formal),
        "near_signal": len(near),
        "bytes": len(payload),
        "sha256": sha256_file(latest_path),
        "elapsed_seconds": round(elapsed, 3),
        "peak_rss_mb": round(peak_end_kb / 1024, 3),
        "peak_rss_increase_after_import_mb": round(max(0, peak_end_kb - peak_start_kb) / 1024, 3),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
