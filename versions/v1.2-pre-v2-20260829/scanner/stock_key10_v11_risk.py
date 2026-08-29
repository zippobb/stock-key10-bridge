#!/usr/bin/env python3
"""Build the formal V1.1 risk layer without changing V1.0 scores."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import resource
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT_DEFAULT = Path("/opt/stock-automation/scanner")
DATE_RE = re.compile(r"^[0-9]{8}$")
TS_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def risk_status(original: dict[str, object], profit: dict[str, object], downgrade: dict[str, object], config: dict[str, object]) -> tuple[str, list[str]]:
    mapping = config["risk_mapping"]
    original_status = str(original.get("risk_status", "PASS"))
    reasons: list[str] = []
    hard = original_status in set(mapping["fail_original_statuses"]) or bool(original.get("direct_excluded", False))
    if hard:
        reasons.append(f"V1.0硬风险={original_status}")
        original_reason = str(original.get("risk_reasons") or "")
        if original_reason and original_reason != "未触发风险":
            reasons.append(original_reason)
        return "FAIL", reasons
    if original_status in set(mapping["warning_original_statuses"]):
        reasons.append(f"V1.0风险状态={original_status}")
    if bool(mapping.get("sector_downgrade_is_warning")) and bool(downgrade.get("triggered")):
        reasons.extend(str(item) for item in downgrade.get("reasons", []))
    if str(profit.get("level")) in set(mapping["warning_profit_taking_levels"]):
        reasons.append(f"获利回吐风险={profit.get('level')}")
        reasons.extend(str(item) for item in profit.get("flags", []))
    return ("WARNING", list(dict.fromkeys(reasons))) if reasons else ("PASS", ["未触发V1.1正式风险"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build formal V1.1 risk results")
    parser.add_argument("--root", default=str(ROOT_DEFAULT))
    parser.add_argument("--config")
    parser.add_argument("--latest-path")
    parser.add_argument("--dated-dir")
    parser.add_argument("--force-recompute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    peak_start = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    root = Path(args.root).resolve(strict=True)
    output = root / "output"
    config_dir = root / "config"
    helper = load_module(root / "stock_key10_v11_shadow.py", "stock_key10_v11_legacy_helpers")
    engine = load_module(root / "stock_key10_engine_v1.py", "stock_key10_engine_v1_for_v11")

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
        "risk_strategy": Path(args.config) if args.config else config_dir / "strategy_v1_1_risk.json",
        "feature_engine": root / "stock_key10_engine_v1.py",
        "history": output / "history_daily_latest.csv",
    }
    for path in paths.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"固定输入不存在或为符号链接: {path}")

    risk_config = helper.load_json(paths["risk_strategy"])
    if risk_config.get("layer_status") != "formal_risk":
        raise RuntimeError("V1.1配置未标记为正式风险层")
    _, formal = helper.load_csv_records(paths["technical_shortlist"])
    _, near = helper.load_csv_records(paths["near_signal_candidates"])
    _, observation_rows = helper.load_csv_records(paths["observation_pool"])
    _, focus_rows = helper.load_csv_records(paths["focus_pool"])
    market = helper.load_json(paths["market_environment"])
    market_risk = helper.load_json(paths["market_risk"])
    sectors = helper.load_json(paths["sector_rotation"])
    diagnostics = helper.load_json(paths["daily_diagnostics"])

    observation_codes = [str(row.get("ts_code", "")) for row in observation_rows]
    focus_codes = [str(row.get("ts_code", "")) for row in focus_rows]
    expected_observation = int(risk_config["expected_observation_pool_count"])
    expected_focus = int(risk_config["expected_focus_pool_count"])
    if len(observation_codes) != expected_observation or len(set(observation_codes)) != expected_observation:
        raise RuntimeError("观察池数量或唯一性异常")
    if len(focus_codes) != expected_focus or len(set(focus_codes)) != expected_focus or not set(focus_codes).issubset(observation_codes):
        raise RuntimeError("重点池数量、唯一性或从属关系异常")
    if any(not TS_CODE_RE.fullmatch(code) for code in observation_codes):
        raise RuntimeError("观察池存在非法ts_code")

    records: list[tuple[str, dict[str, object]]] = [("formal", row) for row in formal] + [("near_signal", row) for row in near]
    codes = [str(row.get("ts_code", "")) for _, row in records]
    if len(codes) != len(set(codes)) or not set(codes).issubset(observation_codes):
        raise RuntimeError("V1.0正式与近似候选重复、交叉或越出观察池")
    dates = {str(row.get("trade_date", "")) for _, row in records} or {str(diagnostics.get("target_trade_date", ""))}
    if len(dates) != 1 or not DATE_RE.fullmatch(next(iter(dates))):
        raise RuntimeError(f"候选交易日异常: {sorted(dates)}")
    trade_date = next(iter(dates))
    for label, value in {
        "market_environment": market.get("trade_date"),
        "market_risk": market_risk.get("trade_date"),
        "sector_rotation": sectors.get("trade_date"),
        "daily_diagnostics": diagnostics.get("target_trade_date"),
    }.items():
        if str(value) != trade_date:
            raise RuntimeError(f"{label}交易日不一致: {value}")

    cache = root / "data/v11/observation_history_cache.csv"
    cache_meta_path = root / "data/v11/observation_history_cache_meta.json"
    diag_inputs = diagnostics.get("input_sha256_after", {})
    history_sha = diag_inputs.get("history") if isinstance(diag_inputs, dict) else None
    if not isinstance(history_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", history_sha):
        history_sha = helper.sha256_file(paths["history"])
    cache_meta = helper.ensure_history_cache(paths["history"], paths["observation_pool"], cache, cache_meta_path, set(observation_codes), history_sha, False)
    history, future_guard, target_cache = helper.load_observation_history_light(cache, cache_meta, root / "data/history_daily", set(observation_codes), trade_date)
    candidate_history = history.loc[history["ts_code"].isin(set(codes))].copy()
    features = engine.observation_features(candidate_history, trade_date)
    if len(features) != len(codes) or features["ts_code"].duplicated().any():
        raise RuntimeError("V1.1候选特征数量或唯一性异常")

    base = pd.read_csv(paths["base_universe"], usecols=["ts_code", "name", "industry", "segment"], dtype={"ts_code": "string"}, low_memory=False)
    observation = pd.DataFrame(observation_rows)
    observation["ts_code"] = observation["ts_code"].astype("string")
    history["_close"] = pd.to_numeric(history["close"], errors="coerce")
    history["_return5"] = history.groupby("ts_code", sort=False)["_close"].pct_change(periods=5, fill_method=None) * 100
    pool5 = history.groupby("ts_code", sort=False).tail(1)[["ts_code", "_return5"]]
    pool = observation[["ts_code", "is_focus"]].merge(base, on="ts_code", how="left", validate="one_to_one").merge(pool5, on="ts_code", how="left", validate="one_to_one")
    pool["return_5d_industry_percentile"] = pool.groupby("industry", dropna=False)["_return5"].rank(pct=True, method="average") * 100
    meta = pool.loc[pool["ts_code"].isin(set(codes)), ["ts_code", "is_focus", "name", "industry", "segment", "return_5d_industry_percentile"]]
    feature_frame = meta.merge(features, on="ts_code", how="left", validate="one_to_one")
    all_sector_rows = sectors.get("all_sectors")
    if not isinstance(all_sector_rows, list):
        raise RuntimeError("板块轮动缺少all_sectors")
    sector_by_industry = {str(row.get("industry")): row for row in all_sector_rows if isinstance(row, dict)}
    feature_frame["sector_rank_percentile"] = feature_frame["industry"].map(lambda value: finite(sector_by_industry.get(str(value), {}).get("sector_rank_percentile")))
    feature_frame["close_vs_ma20_atr"] = np.where(feature_frame["atr14_pct"].gt(0), feature_frame["distance_ma20_pct"].clip(lower=0) / feature_frame["atr14_pct"], np.nan)
    feature_frame["ma5_ma10_gap_atr"] = np.where(feature_frame["atr14_pct"].gt(0), feature_frame["ma5_ma10_gap_pct"] / feature_frame["atr14_pct"], np.nan)
    feature_frame["return_1d_pct"] = pd.to_numeric(feature_frame["pct_chg"], errors="coerce")
    market_median = finite(market.get("market_median_return_1d_pct"))
    if market_median is None:
        raise RuntimeError("市场收益中位数缺失")
    feature_frame["relative_market_strength_1d_pct"] = feature_frame["return_1d_pct"] - market_median
    by_code = {str(row["ts_code"]): row.to_dict() for _, row in feature_frame.iterrows()}

    candidates: list[dict[str, object]] = []
    status_counter: Counter[str] = Counter()
    for layer, original in records:
        code = str(original["ts_code"])
        metrics = by_code[code]
        metrics["primary_signal"] = original.get("primary_signal")
        metrics["near_signal_family"] = original.get("near_signal_family")
        sector_name = str(metrics["industry"])
        sector_row = sector_by_industry.get(sector_name)
        if not isinstance(sector_row, dict):
            raise RuntimeError(f"候选板块无法匹配: {code} {sector_name}")
        stage = helper.sector_stage(sector_row, risk_config)
        setup = helper.setup_type(metrics, stage, risk_config)
        profit = helper.profit_taking_risk(metrics, setup, stage, risk_config)
        downgrade = helper.sector_downgrade(metrics, stage, sector_row, risk_config)
        scenario = helper.scenario_type(setup, stage, metrics, market)
        status, reasons = risk_status(original, profit, downgrade, risk_config)
        status_counter[status] += 1
        candidates.append(helper.clean({
            "candidate_layer": layer,
            "ts_code": code,
            "name": original.get("name") or metrics.get("name"),
            "is_focus": bool(original.get("is_focus")),
            "industry_sector": sector_name,
            "v1_0": original,
            "setup_type": setup,
            "sector_stage": stage,
            "profit_taking_risk": profit,
            "sector_downgrade": downgrade,
            "scenario_type": scenario,
            "v1_1_risk_status": status,
            "v1_1_risk_reasons": reasons,
            "hard_risk_can_be_offset": False,
            "theme_tags": None,
            "dominant_driver": None,
            "event_risk": None,
            "fundamental_review": None,
            "review_pending": True,
        }))

    sources = {name: helper.sha256_file(path) for name, path in paths.items() if name != "history"}
    sources.update({"history": history_sha, "observation_history_cache": helper.sha256_file(cache), "target_daily_cache": helper.sha256_file(target_cache)})
    latest = Path(args.latest_path) if args.latest_path else output / "key10_v11_risk_latest.json"
    dated_dir = Path(args.dated_dir) if args.dated_dir else root / "data/v11"
    dated = dated_dir / f"key10_v11_risk_{trade_date}.json"
    if not args.force_recompute and dated.is_file() and not dated.is_symlink():
        existing = helper.load_json(dated)
        if (
            existing.get("schema_version") == risk_config.get("schema_version")
            and existing.get("layer_status") == "formal_risk"
            and existing.get("trade_date") == trade_date
            and existing.get("source_file_sha256") == sources
            and int(existing.get("future_data_rows", -1)) == 0
            and existing.get("completion_metrics_removed") is True
        ):
            payload = dated.read_bytes()
            helper.atomic_pair(latest, dated, payload)
            print(json.dumps({"status": "success_idempotent", "trade_date": trade_date, "risk_status_counts": existing.get("risk_status_counts"), "bytes": len(payload), "sha256": helper.sha256_file(latest)}, ensure_ascii=False, indent=2))
            return 0
    result = helper.clean({
        "schema_version": risk_config["schema_version"],
        "layer_status": "formal_risk",
        "generated_at": helper.now_bj().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "observation_pool_count": len(observation_codes),
        "focus_pool_count": len(focus_codes),
        "source_file_sha256": sources,
        "candidate_counts": {"formal": len(formal), "near_signal": len(near), "total": len(candidates)},
        "risk_status_counts": dict(status_counter),
        "candidates": candidates,
        "completion_metrics_removed": True,
        "v1_0_score_or_order_changed": False,
        "pool_changed": False,
        "future_data_rows": int(future_guard["future_data_rows_used"]),
        "hard_risk_can_be_offset": False,
        "performance": {
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
            "peak_rss_increase_mb": round(max(0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - peak_start) / 1024, 3),
        },
    })
    if result["future_data_rows"] != 0:
        raise RuntimeError("future_data_rows非零，拒绝写入")
    payload = helper.json_bytes(result)
    helper.atomic_pair(latest, dated, payload)
    print(json.dumps({"status": "success", "trade_date": trade_date, "risk_status_counts": dict(status_counter), "bytes": len(payload), "sha256": helper.sha256_file(latest), "elapsed_seconds": result["performance"]["elapsed_seconds"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
