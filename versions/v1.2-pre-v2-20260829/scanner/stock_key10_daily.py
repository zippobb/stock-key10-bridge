#!/usr/bin/env python3
"""Autonomous, idempotent scheduler entry for the key10 technical pipeline.

The dated 20260820 runner is retained unchanged as the audited formula engine.
This entry supplies dynamic trade dates, transaction-like output commits, a
process lock, and machine-readable automation status files.
"""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import math
import os
import resource
import shutil
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import tushare as ts


ROOT = Path("/opt/stock-automation/scanner")
ENGINE_PATH = ROOT / "stock_key10_engine_v1.py"
LOCK_PATH = Path("/run/lock/stock-key10-daily.lock")
STATUS_LATEST = ROOT / "output/automation_status_latest.json"
STATUS_DIR = ROOT / "data/automation"
TZ = ZoneInfo("Asia/Shanghai")
EXCLUDED_STOCK_FIELD = "three_days_no_break_prior_20d_low"
SUCCESS_DIAG_PATTERN = "key10_daily_diagnostics_*.json"
MAX_CATCH_UP_DAYS = 10
EXPECTED_OBSERVATION_POOL_COUNT = 237
EXPECTED_FOCUS_POOL_COUNT = 83


def now_bj() -> datetime:
    return datetime.now(TZ)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"


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


def safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for key in ("TUSHARE_TOKEN", "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET"):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "***")
    return text[:1200]


def backup_file(path: Path, stamp: str) -> str | None:
    if not path.is_file():
        return None
    backup = path.with_name(f"{path.name}.bak.STEP6A_{stamp}")
    shutil.copy2(path, backup)
    return str(backup)


def write_status(status: dict[str, object], date_key: str) -> None:
    payload = json_bytes(status)
    date_path = STATUS_DIR / f"automation_status_{date_key}.json"
    stamp = now_bj().strftime("%Y%m%d_%H%M%S_%f")
    backup_file(STATUS_LATEST, stamp)
    backup_file(date_path, stamp)
    atomic_write(STATUS_LATEST, payload)
    atomic_write(date_path, payload)


def load_engine():
    if not ENGINE_PATH.is_file():
        raise FileNotFoundError(f"审计公式引擎不存在: {ENGINE_PATH}")
    spec = importlib.util.spec_from_file_location("stock_key10_engine_v1", ENGINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载审计公式引擎")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def last_successful_trade_date() -> str | None:
    dates: list[str] = []
    for path in (ROOT / "data/key10").glob(SUCCESS_DIAG_PATTERN):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            date = str(data.get("target_trade_date", ""))
            validation = data.get("validation", {})
            if len(date) == 8 and date.isdigit() and validation.get("future_data_rows_used", 0) == 0:
                dates.append(date)
        except Exception:
            continue
    return max(dates) if dates else None


def trading_calendar() -> pd.DataFrame:
    token = <REDACTED>
    if not token:
        raise RuntimeError("TUSHARE_TOKEN环境变量不可用")
    today = now_bj().date()
    start = (today - timedelta(days=80)).strftime("%Y%m%d")
    end = (today + timedelta(days=45)).strftime("%Y%m%d")
    frame = ts.pro_api(token).trade_cal(exchange="SSE", start_date=start, end_date=end)
    if frame is None or frame.empty:
        raise RuntimeError("交易日历不可用")
    required = {"cal_date", "is_open"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"交易日历字段不足: {sorted(required - set(frame.columns))}")
    frame = frame.copy()
    frame["cal_date"] = frame["cal_date"].astype(str).str.replace("-", "", regex=False)
    frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce")
    frame.sort_values("cal_date", kind="stable", inplace=True)
    return frame


def date_plan(calendar: pd.DataFrame) -> dict[str, object]:
    current = now_bj()
    today = current.strftime("%Y%m%d")
    completed_cutoff = today if current.time() >= dt_time(18, 15) else (current.date() - timedelta(days=1)).strftime("%Y%m%d")
    opens = calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str).tolist()
    completed = [date for date in opens if date <= completed_cutoff]
    if not completed:
        raise RuntimeError("交易日历窗口内没有已完成交易日")
    latest_completed = completed[-1]
    last_success = last_successful_trade_date()
    pending = [date for date in completed if last_success is None or date > last_success]
    if len(pending) > MAX_CATCH_UP_DAYS:
        raise RuntimeError(f"待补交易日{len(pending)}个，超过安全上限{MAX_CATCH_UP_DAYS}，停止")
    is_today_open = bool(((calendar["cal_date"] == today) & calendar["is_open"].eq(1)).any())
    return {
        "today": today,
        "is_today_open": is_today_open,
        "latest_completed_trade_date": latest_completed,
        "last_successful_trade_date": last_success,
        "pending_trade_dates": pending,
        "calendar": calendar,
    }


def adjacent_dates(calendar: pd.DataFrame, target: str) -> tuple[str, str]:
    opens = calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str).tolist()
    before = [date for date in opens if date < target]
    after = [date for date in opens if date > target]
    if not before or not after:
        raise RuntimeError(f"无法确定{target}相邻交易日")
    return before[-1], after[0]


def csv_payload(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, lineterminator="\n", float_format="%.6f").encode("utf-8")


def sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    return value


def stage_and_commit(payloads: dict[Path, bytes]) -> list[str]:
    stamp = now_bj().strftime("%Y%m%d_%H%M%S_%f")
    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    for path, payload in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        staged[path] = Path(temp_name)
        if path.is_file():
            backup = path.with_name(f"{path.name}.bak.STEP6A_{stamp}")
            shutil.copy2(path, backup)
            backups[path] = backup
    try:
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
            committed.append(path)
    except Exception:
        for path in committed:
            failed = path.with_name(f"{path.name}.failed.STEP6A_{stamp}")
            os.replace(path, failed)
            if path in backups:
                shutil.copy2(backups[path], path)
        for path, temp_path in staged.items():
            if temp_path.exists():
                failed = path.with_name(f"{path.name}.failed.STEP6A_{stamp}")
                os.replace(temp_path, failed)
        raise
    return [str(backups[path]) for path in backups]


def required_input_paths(previous_date: str) -> dict[str, Path]:
    return {
        "observation": ROOT / "config/observation_pool.csv",
        "focus": ROOT / "config/focus_pool.csv",
        "base": ROOT / "output/base_universe_latest.csv",
        "history": ROOT / "output/history_daily_latest.csv",
        "config": ROOT / "strategy_v1.json",
        "v1_2_config": ROOT / "config/strategy_v1_2.json",
        "branch1": ROOT / "branch1_selector.py",
        "previous_sector_rotation": ROOT / f"data/sector/sector_rotation_{previous_date}.json",
        "previous_diagnostics": ROOT / f"data/key10/key10_daily_diagnostics_{previous_date}.json",
        "formula_engine": ENGINE_PATH,
    }


def compute_trade_date(target_date: str, previous_date: str, next_date: str) -> dict[str, object]:
    started = time.perf_counter()
    engine = load_engine()
    engine.TARGET_DATE = target_date
    engine.PREVIOUS_DATE = previous_date
    engine.FINAL10 = []
    paths = required_input_paths(previous_date)
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"输入不存在: {label}={path}")
    input_hashes_before = {label: sha256(path) for label, path in paths.items()}

    observation = pd.read_csv(paths["observation"], dtype={"ts_code": "string", "code": "string"})
    focus = pd.read_csv(paths["focus"], dtype={"ts_code": "string", "code": "string"})
    if len(observation) != EXPECTED_OBSERVATION_POOL_COUNT or len(focus) != EXPECTED_FOCUS_POOL_COUNT:
        raise ValueError(f"股票池数量异常: observation={len(observation)}, focus={len(focus)}")
    if observation["ts_code"].duplicated().any() or focus["ts_code"].duplicated().any():
        raise ValueError("股票池存在重复ts_code")
    if not set(focus["ts_code"]).issubset(set(observation["ts_code"])):
        raise ValueError("重点池不是观察池子集")
    if int(pd.to_numeric(observation["is_focus"], errors="coerce").sum()) != EXPECTED_FOCUS_POOL_COUNT:
        raise ValueError(f"观察池重点标签不是{EXPECTED_FOCUS_POOL_COUNT}只")

    base = pd.read_csv(paths["base"], dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
    engine.require_columns(base, ["ts_code", "name", "industry", "segment"], "基础股票池")
    if base["ts_code"].duplicated().any():
        raise ValueError("基础股票池ts_code重复")
    observation_codes = set(observation["ts_code"])
    base_codes = set(base["ts_code"])
    if not observation_codes.issubset(base_codes):
        raise ValueError(f"观察池与基础股票列表未匹配{len(observation_codes - base_codes)}只")
    config = json.loads(paths["config"].read_text(encoding="utf-8"))
    v1_2_config = json.loads(paths["v1_2_config"].read_text(encoding="utf-8"))
    if EXCLUDED_STOCK_FIELD not in config.get("exclude_from_stock_scoring", []):
        raise ValueError("three_days_no_break_prior_20d_low排除配置缺失")
    branch1 = engine.load_branch1(paths["branch1"])
    previous_sector = json.loads(paths["previous_sector_rotation"].read_text(encoding="utf-8"))
    previous_diagnostics = json.loads(paths["previous_diagnostics"].read_text(encoding="utf-8"))
    previous_market = previous_diagnostics.get("market_environment", {})
    if str(previous_market.get("trade_date")) != previous_date or str(previous_sector.get("trade_date")) != previous_date:
        raise ValueError("上一交易日市场或板块诊断日期不一致")

    daily_cache = ROOT / f"data/history_daily/daily_{target_date}.csv"
    daily, daily_source = engine.fetch_daily_once(target_date, daily_cache)
    missing_observation = sorted(observation_codes - set(daily["ts_code"]))
    if missing_observation:
        raise ValueError(f"目标日观察池行情缺失{len(missing_observation)}只: {missing_observation}")
    index_window = engine.fetch_major_index_window(target_date, previous_date)
    recent, market_dates = engine.load_recent_market_history(ROOT / "data/history_daily", daily, target_date, base_codes)
    if market_dates[-1] != target_date or previous_date not in market_dates:
        raise ValueError("轻量市场窗口日期异常")
    light = engine.add_light_features(recent)
    market = engine.build_market_environment(light, market_dates, config, base, v1_2_config["market_bottom"])
    sector_current, sector_json = engine.build_sector_rotation(light, base, previous_date, target_date)
    engine.annotate_sector_rotation(sector_json)
    repair, risk_warning = engine.build_market_repair_diagnostics(index_window, previous_market, market, sector_json)
    market = engine.finalize_market_bottom(market, index_window, repair, sector_json, v1_2_config["market_bottom"])
    risk_warning["trade_date"] = target_date
    risk_warning["next_trade_date"] = next_date
    risk_warning["execution_advice"] = (
        "市场风险不作绝对开关；PASS优先。若修复不足或风险为警戒/高风险，WARNING必须等待9:45后回踩和量价确认。"
    )
    market["market_repair_status"] = repair["market_repair_status"]
    market["market_repair_diagnostics"] = repair
    policies = {
        "上涨": "市场状态不作硬开关，可正常筛选，仍避免高开追价。",
        "横盘震荡": "市场状态不作硬开关，优先缩量守线和右侧修复。",
        "下跌中反弹": "优先相对强势板块与领先股，WARNING必须等待确认。",
        "下跌": "不关闭选股，只保留独立走强个股并降低追价意愿。",
    }
    market["candidate_policy"] = policies[market["market_state"]]
    market["market_summary"] = (
        f"{target_date}市场原始分{market['market_score_raw']:.2f}、3日平滑分{market['market_score_3d_smoothed']:.2f}，"
        f"趋势状态为{market['market_state']}，底部阶段为{market['market_bottom_status']}。"
        f"当日上涨{market['advance_count']}只、下跌{market['decline_count']}只，收益中位数{market['market_median_return_1d_pct']:.2f}%；"
        f"沪深全部A股成交额{float(market['sh_sz_all_a_amount_thousand_yuan'])/100000:.0f}亿元。"
    )
    sector_json["market_context"] = {
        "market_score_raw": market["market_score_raw"],
        "market_score_3d_smoothed": market["market_score_3d_smoothed"],
        "market_state": market["market_state"],
        "market_bottom_status": market["market_bottom_status"],
        "market_repair_status": repair["market_repair_status"],
    }

    history_obs, future_guard = engine.load_observation_history(
        paths["history"], ROOT / "data/history_daily", observation_codes, daily, target_date
    )
    features = engine.observation_features(history_obs, target_date)
    if len(features) != EXPECTED_OBSERVATION_POOL_COUNT or features["ts_code"].duplicated().any():
        raise ValueError(f"观察池完整特征异常: rows={len(features)}, dup={features['ts_code'].duplicated().sum()}")
    scored = engine.score_observation(features, observation, base, sector_current, market, config, branch1)
    market_median = float(market["market_median_return_1d_pct"])
    scored["relative_market_strength_1d_pct"] = (scored["return_1d_pct"] - market_median).round(4)
    scored["next_day_buyability"] = scored.apply(
        lambda row: engine.next_day_buyability(row, repair["market_repair_status"]), axis=1
    )
    minimum = float(config["score_weights"]["watch_candidate_min"])
    formal_mask = (
        ~scored["direct_excluded"]
        & scored["signal_count"].ge(1)
        & scored["total_score"].ge(minimum)
        & ~scored["risk_status"].eq("VETO")
    )
    all_formal = scored.loc[formal_mask].copy()
    all_formal["risk_priority"] = all_formal["risk_status"].map({"PASS": 0, "WARNING": 1}).fillna(9)
    all_formal.sort_values(
        ["risk_priority", "total_score", "relative_strength_score", "historical_elasticity_percentile", "ts_code"],
        ascending=[True, False, False, False, True], na_position="last", kind="stable", inplace=True,
    )
    candidates = all_formal.head(20).copy()
    candidates.insert(0, "rank", range(1, len(candidates) + 1))
    candidates["selection_reasons"] = candidates.apply(branch1.selection_reason, axis=1)
    candidates["fundamental_review_pending"] = True
    shortlist_columns = [
        "rank", "trade_date", "ts_code", "name", "is_focus", "close", "sector", "total_score",
        "pattern_score", "volume_price_score", "stage_score", "sector_score", "relative_strength_score",
        "matched_signal", "primary_signal", "risk_status", "risk_reasons", "market_state", "market_score",
        "market_bottom_status", "sector_rank", "sector_rank_1d", "sector_rank_3d", "sector_rotation_status",
        "sector_rank_percentile", "return_1d_pct", "return_5d_pct", "return_20d_pct",
        "relative_market_strength_1d_pct", "historical_elasticity_percentile", "atr14_pct",
        "close_vs_ma20_atr", "ma5_ma10_gap_atr", "ma5_slope_3d_pct", "ma5_slope_change_pct",
        "upper_shadow_pct", "overheat_signal_count", "next_day_buyability",
        "fundamental_review_pending", "selection_reasons",
    ]
    shortlist = candidates[shortlist_columns].copy()
    near_candidates = engine.build_near_signal_watch(scored, set(shortlist["ts_code"]), market_median)
    near_watch = near_candidates if len(shortlist) < 15 else near_candidates.head(0).copy()

    validation = {
        "shortlist_duplicate_ts_code": int(shortlist["ts_code"].duplicated().sum()),
        "near_duplicate_ts_code": int(near_watch["ts_code"].duplicated().sum()) if "ts_code" in near_watch else 0,
        "shortlist_below_60": int(shortlist["total_score"].lt(60).sum()),
        "shortlist_veto_count": int(shortlist["risk_status"].eq("VETO").sum()),
        "all_shortlist_from_observation": bool(set(shortlist["ts_code"]).issubset(observation_codes)),
        "max_history_date_used": str(history_obs["trade_date"].max()),
        "future_data_rows_used": int(history_obs["trade_date"].gt(target_date).sum()),
        "index_future_rows_used": int(index_window["trade_date"].gt(target_date).sum()),
    }
    if any([
        validation["shortlist_duplicate_ts_code"], validation["near_duplicate_ts_code"],
        validation["shortlist_below_60"], validation["shortlist_veto_count"],
        validation["future_data_rows_used"], validation["index_future_rows_used"],
    ]) or not validation["all_shortlist_from_observation"]:
        raise RuntimeError(f"提交前验证失败: {validation}")

    shortlist_latest = ROOT / "output/key10_technical_shortlist_latest.csv"
    shortlist_date = ROOT / f"data/key10/key10_technical_shortlist_{target_date}.csv"
    near_latest = ROOT / "output/key10_near_signal_watch_latest.csv"
    near_date = ROOT / f"data/key10/key10_near_signal_watch_{target_date}.csv"
    market_latest = ROOT / "output/key10_market_environment_latest.json"
    market_date = ROOT / f"data/market/key10_market_environment_{target_date}.json"
    sector_latest = ROOT / "output/sector_rotation_latest.json"
    sector_date = ROOT / f"data/sector/sector_rotation_{target_date}.json"
    risk_latest = ROOT / "output/market_risk_warning_latest.json"
    risk_date = ROOT / f"data/market/market_risk_warning_{target_date}.json"
    diag_latest = ROOT / "output/key10_daily_diagnostics_latest.json"
    diag_date = ROOT / f"data/key10/key10_daily_diagnostics_{target_date}.json"

    shortlist_payload = csv_payload(shortlist)
    near_payload = csv_payload(near_watch)
    market_payload = json_bytes(sanitize(market))
    sector_payload = json_bytes(sanitize(sector_json))
    risk_payload = json_bytes(sanitize(risk_warning))
    non_diag_payloads = {
        shortlist_latest: shortlist_payload, shortlist_date: shortlist_payload,
        near_latest: near_payload, near_date: near_payload,
        market_latest: market_payload, market_date: market_payload,
        sector_latest: sector_payload, sector_date: sector_payload,
        risk_latest: risk_payload, risk_date: risk_payload,
    }
    input_hashes_after = {label: sha256(path) for label, path in paths.items()}
    unexpected = [label for label in input_hashes_before if input_hashes_before[label] != input_hashes_after[label]]
    if unexpected:
        raise RuntimeError(f"输入文件意外变化: {unexpected}")

    score_ge_60_before_risk = int((
        ~scored["direct_excluded"] & scored["signal_count"].ge(1) & scored["total_score"].ge(minimum)
    ).sum())
    veto_mask = (
        ~scored["direct_excluded"] & scored["signal_count"].ge(1)
        & scored["total_score"].ge(minimum) & scored["risk_status"].eq("VETO")
    )
    diagnostics = {
        "run_time": now_bj().isoformat(timespec="seconds"),
        "target_trade_date": target_date,
        "previous_trade_date": previous_date,
        "next_trade_date": next_date,
        "stable_entry": str(ROOT / "stock_key10_daily.py"),
        "formula_engine": {"path": str(ENGINE_PATH), "sha256": sha256(ENGINE_PATH)},
        "scope_confirmation": {
            "observation_only_full_features": True,
            "full_market_single_daily_snapshot_only": True,
            "full_market_v1_recomputed": False,
            "full_market_elasticity_recomputed": False,
            "focus_affects_score": False,
            "excluded_stock_field": EXCLUDED_STOCK_FIELD,
            "excluded_field_used_in_stock_formula": False,
            "formal_rules_modified": False,
            "d1_final10_review_run": False,
            "fundamental_review_run": False,
            "policy_news_fetch_run": False,
        },
        "pool_validation": {
            "observation_count": len(observation), "focus_count": len(focus),
            "observation_duplicates": int(observation["ts_code"].duplicated().sum()),
            "focus_duplicates": int(focus["ts_code"].duplicated().sum()),
            "focus_not_observation": len(set(focus["ts_code"]) - set(observation["ts_code"])),
            "observation_missing_target_daily": len(missing_observation),
        },
        "data_fetch": {
            "daily_source": daily_source, "daily_rows": len(daily),
            "daily_unique_ts_code": int(daily["ts_code"].nunique()),
            "full_market_daily_requests_this_run": 0 if daily_source == "existing_valid_cache" else 1,
            "major_index_queries": 3,
            "market_cache_dates_read": market_dates,
            "full_historical_api_requests": 0,
            "intraday_requests": 0,
        },
        "market_environment": sanitize(market),
        "market_repair_diagnostics": sanitize(repair),
        "market_risk_warning": sanitize(risk_warning),
        "sector_rotation_summary": sanitize({
            key: sector_json[key] for key in [
                "sector_count", "top_quartile_cutoff_rank", "top5", "newly_strong_sectors",
                "weakening_sectors", "exited_strong_sectors", "important_rotation_changes"
            ]
        }),
        "yesterday_final10_review": {
            "status": "not_run_by_technical_scheduler",
            "reason": "服务器没有ChatGPT最终10只的权威输入文件；未伪造复盘，既有复盘输出未被覆盖。",
        },
        "today_selection": {
            "formal_eligible_count_before_top20": len(all_formal),
            "formal_output_count": len(shortlist),
            "near_signal_layer_triggered": len(shortlist) < 15,
            "near_signal_count": len(near_watch),
            "signaled_count": int(scored["signal_count"].ge(1).sum()),
            "score_ge_60_before_risk": score_ge_60_before_risk,
            "risk_veto_count": int(veto_mask.sum()),
            "risk_veto_reason_counts": dict(Counter("；".join(scored.loc[veto_mask, "risk_reasons"].astype(str)).split("；"))) if veto_mask.any() else {},
            "risk_status_counts": {str(k): int(v) for k, v in shortlist["risk_status"].value_counts().to_dict().items()},
        },
        "future_data_guard": sanitize(future_guard),
        "input_sha256_before": input_hashes_before,
        "input_sha256_after": input_hashes_after,
        "unexpected_input_changes": unexpected,
        "validation": validation,
        "performance": {
            "elapsed_seconds_before_commit": round(time.perf_counter() - started, 3),
            "peak_rss_kb": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        },
        "output_files": [str(path) for path in [*non_diag_payloads, diag_latest, diag_date]],
    }
    diagnostics["output_sha256_excluding_diagnostics"] = {
        str(path): hashlib.sha256(payload).hexdigest() for path, payload in non_diag_payloads.items()
    }
    diag_payload = json_bytes(sanitize(diagnostics))
    payloads = {**non_diag_payloads, diag_latest: diag_payload, diag_date: diag_payload}
    backups = stage_and_commit(payloads)

    pairs = [
        (shortlist_latest, shortlist_date), (near_latest, near_date), (market_latest, market_date),
        (sector_latest, sector_date), (risk_latest, risk_date), (diag_latest, diag_date),
    ]
    if not all(left.read_bytes() == right.read_bytes() for left, right in pairs):
        raise RuntimeError("latest与日期版提交后不一致")
    temp_residuals = [str(path) for folder in {path.parent for path in payloads} for path in folder.glob(".*.tmp")]
    if temp_residuals:
        raise RuntimeError(f"发现临时文件残留: {temp_residuals}")
    return {
        "target_trade_date": target_date,
        "previous_trade_date": previous_date,
        "next_trade_date": next_date,
        "formal_candidate_count": len(shortlist),
        "near_signal_count": len(near_watch),
        "market_state": market["market_state"],
        "market_score_raw": market["market_score_raw"],
        "market_score_3d_smoothed": market["market_score_3d_smoothed"],
        "market_bottom_status": market["market_bottom_status"],
        "market_risk_level": risk_warning["current_risk_level"] if "current_risk_level" in risk_warning else risk_warning.get("risk_level"),
        "output_files": [{"path": str(path), "sha256": sha256(path)} for path in payloads],
        "backups_created": backups,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "validation": validation,
    }


def main() -> int:
    os.chdir(ROOT)
    started_at = now_bj()
    started = time.perf_counter()
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("已有 stock-key10-daily 实例运行，本次正常跳过。")
        return 0

    target_hint = started_at.strftime("%Y%m%d")
    try:
        calendar = trading_calendar()
        plan = date_plan(calendar)
        pending = list(plan["pending_trade_dates"])
        if not pending:
            ended = now_bj()
            status = {
                "schema_version": "1.0", "automation": "stock-key10-daily",
                "status": "skipped", "skip_reason": "non_trading_day_or_already_completed",
                "started_at": started_at.isoformat(timespec="seconds"),
                "ended_at": ended.isoformat(timespec="seconds"), "exit_code": 0,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "target_trade_date": None,
                "latest_completed_trade_date": plan["latest_completed_trade_date"],
                "last_successful_trade_date": plan["last_successful_trade_date"],
                "formal_candidate_count": None, "near_signal_count": None,
                "market_state": None, "market_risk_level": None,
                "output_files": [], "error_summary": None,
                "today_is_trading_day": plan["is_today_open"],
            }
            write_status(status, plan["today"])
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0

        results = []
        for target in pending:
            previous, next_date = adjacent_dates(calendar, target)
            target_hint = target
            result = compute_trade_date(target, previous, next_date)
            results.append(result)
            interim = {
                "schema_version": "1.0", "automation": "stock-key10-daily", "status": "success",
                "started_at": started_at.isoformat(timespec="seconds"),
                "ended_at": now_bj().isoformat(timespec="seconds"), "exit_code": 0,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "processed_trade_dates": [item["target_trade_date"] for item in results],
                **result, "error_summary": None,
            }
            write_status(interim, target)
        print(json.dumps(sanitize(interim), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        ended = now_bj()
        status = {
            "schema_version": "1.0", "automation": "stock-key10-daily", "status": "failed",
            "started_at": started_at.isoformat(timespec="seconds"),
            "ended_at": ended.isoformat(timespec="seconds"), "exit_code": 1,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "target_trade_date": target_hint,
            "formal_candidate_count": None, "near_signal_count": None,
            "market_state": None, "market_risk_level": None, "output_files": [],
            "error_summary": safe_error(exc),
            "previous_success_outputs_preserved": True,
        }
        try:
            write_status(status, target_hint)
        except Exception as status_exc:
            print(f"状态写入失败: {safe_error(status_exc)}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
