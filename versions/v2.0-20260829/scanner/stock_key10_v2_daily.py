#!/usr/bin/env python3
"""V2.0 21:30 quantitative preselection.

Only the confirmed observation pool can produce official review candidates.
The external scan is a separate reserve list and never mutates the pool.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
import traceback
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts

from stock_key10_v2_common import json_bytes, load_json, now_bj, safe_error, sha256, write_json_pair
import stock_key10_v2_data as data_adapter
from stock_key10_v2_scoring import build_sector_scores, build_stock_features, score_stocks, strong_sectors


ROOT = Path(os.environ.get("STOCK_AUTOMATION_ROOT", "/opt/stock-automation/scanner"))
CONFIG_PATH = ROOT / "config/strategy_v2.json"
LOCK_PATH = Path("/run/lock/stock-key10-v2-daily.lock")
STATUS_LATEST = ROOT / "output/key10_v2_automation_status_latest.json"
TZ = ZoneInfo("Asia/Shanghai")
MAX_CATCH_UP_DAYS = 10


def trading_calendar() -> pd.DataFrame:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN环境变量不可用")
    today = now_bj().date()
    frame = ts.pro_api(token).trade_cal(
        exchange="SSE",
        start_date=(today - timedelta(days=80)).strftime("%Y%m%d"),
        end_date=(today + timedelta(days=45)).strftime("%Y%m%d"),
    )
    if frame is None or frame.empty or not {"cal_date", "is_open"}.issubset(frame.columns):
        raise RuntimeError("交易日历不可用")
    frame = frame.copy()
    frame["cal_date"] = frame["cal_date"].astype(str).str.replace("-", "", regex=False)
    frame["is_open"] = pd.to_numeric(frame["is_open"], errors="coerce")
    return frame.sort_values("cal_date", kind="stable")


def completed_dates(calendar: pd.DataFrame) -> list[str]:
    current = now_bj()
    today = current.strftime("%Y%m%d")
    cutoff = today if current.time() >= dt_time(18, 15) else (current.date()-timedelta(days=1)).strftime("%Y%m%d")
    return calendar.loc[calendar["is_open"].eq(1) & calendar["cal_date"].le(cutoff), "cal_date"].astype(str).tolist()


def last_success() -> str | None:
    dates: list[str] = []
    for path in (ROOT / "data/v2").glob("key10_v2_review_payload_*.json"):
        try:
            value = load_json(path)
            date = str(value.get("trade_date", ""))
            if len(date) == 8 and value.get("validation", {}).get("future_data_rows_used") == 0:
                dates.append(date)
        except Exception:
            continue
    return max(dates) if dates else None


def adjacent_dates(calendar: pd.DataFrame, target: str) -> tuple[str, str]:
    opens = calendar.loc[calendar["is_open"].eq(1), "cal_date"].astype(str).tolist()
    before, after = [x for x in opens if x < target], [x for x in opens if x > target]
    if not before or not after:
        raise RuntimeError(f"无法确定{target}的相邻交易日")
    return before[-1], after[0]


def load_history(codes: set[str], target_daily: pd.DataFrame, target_date: str) -> tuple[pd.DataFrame, dict[str, object]]:
    path = ROOT / "output/history_daily_latest.csv"
    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    pieces: list[pd.DataFrame] = []
    rows_scanned = 0
    source_max = ""
    for chunk in pd.read_csv(path, dtype={"ts_code": "string", "trade_date": "string"}, chunksize=150000, low_memory=False):
        missing = set(needed) - set(chunk.columns)
        if missing:
            raise ValueError(f"历史数据缺少字段: {sorted(missing)}")
        rows_scanned += len(chunk)
        chunk["trade_date"] = chunk["trade_date"].astype(str).str.replace("-", "", regex=False)
        source_max = max(source_max, str(chunk["trade_date"].max()))
        selected = chunk.loc[chunk["ts_code"].isin(codes) & chunk["trade_date"].lt(target_date), needed]
        if not selected.empty:
            pieces.append(selected.copy())
    cached_dates: list[str] = []
    for cache in sorted((ROOT / "data/history_daily").glob("daily_*.csv")):
        date = cache.stem.removeprefix("daily_")
        if not (date.isdigit() and source_max < date < target_date):
            continue
        day = pd.read_csv(cache, dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
        missing = set(needed) - set(day.columns)
        if missing:
            raise ValueError(f"缓存{date}缺少字段: {sorted(missing)}")
        day["trade_date"] = day["trade_date"].astype(str).str.replace("-", "", regex=False)
        if set(day["trade_date"]) != {date} or day["ts_code"].duplicated().any():
            raise ValueError(f"缓存{date}日期异常或代码重复")
        pieces.append(day.loc[day["ts_code"].isin(codes), needed].copy())
        cached_dates.append(date)
    today = target_daily.loc[target_daily["ts_code"].isin(codes), needed].copy()
    pieces.append(today)
    history = pd.concat(pieces, ignore_index=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    history.drop_duplicates(["ts_code", "trade_date"], keep="last", inplace=True)
    history = history.groupby("ts_code", group_keys=False).tail(300).copy()
    if history["trade_date"].gt(target_date).any():
        raise ValueError("历史读取使用了目标日后的数据")
    return history, {
        "source_rows_scanned": rows_scanned,
        "source_max_trade_date": source_max,
        "cache_dates_added": cached_dates,
        "target_rows_added": len(today),
        "future_data_rows_used": int(history["trade_date"].gt(target_date).sum()),
    }


def market_snapshot(light: pd.DataFrame, index_history: pd.DataFrame, target_date: str) -> dict[str, object]:
    day = light.loc[light["trade_date"].astype(str).eq(target_date)].copy()
    if day.empty:
        raise ValueError("目标日市场快照为空")
    returns = pd.to_numeric(day["pct_chg"], errors="coerce")
    above = pd.to_numeric(day["close"], errors="coerce").ge(pd.to_numeric(day["ma20"], errors="coerce"))
    amount_yi = pd.to_numeric(day["amount"], errors="coerce").sum() / 100000.0
    up_ratio = float(returns.gt(0).mean())
    above_ratio = float(above.mean())
    median = float(returns.median())
    index_rows: list[dict[str, object]] = []
    positive_index_trends = 0
    for _, group in index_history.groupby("ts_code", sort=True):
        group = group.sort_values("trade_date", kind="stable")
        close = pd.to_numeric(group["close"], errors="coerce")
        ma20 = close.rolling(20, min_periods=20).mean()
        return5 = (close/close.shift(5)-1)*100
        trend = "上涨" if close.iloc[-1] >= ma20.iloc[-1] and return5.iloc[-1] > 0 else "下跌" if close.iloc[-1] < ma20.iloc[-1] and return5.iloc[-1] < 0 else "震荡"
        positive_index_trends += int(trend == "上涨")
        index_rows.append({
            "ts_code": str(group["ts_code"].iloc[-1]), "name": str(group["index_name"].iloc[-1]),
            "close": round(float(close.iloc[-1]), 4), "return_1d_pct": round(float(group["pct_chg"].iloc[-1]), 4),
            "return_5d_pct": round(float(return5.iloc[-1]), 4), "ma20": round(float(ma20.iloc[-1]), 4), "trend": trend,
        })
    if median > 0.5 and up_ratio >= 0.60 and above_ratio >= 0.55 and positive_index_trends >= 2:
        phase, posture = "趋势延续", "进攻"
    elif median > 0 and up_ratio >= 0.52 and positive_index_trends >= 1:
        phase, posture = "修复", "进攻"
    elif median < -0.5 and up_ratio <= 0.40:
        phase, posture = "退潮", "防守"
    else:
        phase, posture = "震荡", "防守"
    return {
        "trade_date": target_date,
        "phase": phase,
        "posture": posture,
        "advance_count": int(returns.gt(0).sum()),
        "decline_count": int(returns.lt(0).sum()),
        "unchanged_count": int(returns.eq(0).sum()),
        "advance_ratio_pct": round(up_ratio*100, 4),
        "above_ma20_ratio_pct": round(above_ratio*100, 4),
        "market_median_return_1d_pct": round(median, 4),
        "market_amount_yi": round(float(amount_yi), 4),
        "major_indices": index_rows,
    }


def candidate_records(frame: pd.DataFrame, limit: int, origin: str) -> list[dict[str, object]]:
    columns = [
        "ts_code", "name", "segment", "sector", "trade_date", "close", "setup_type",
        "market_fit_score", "sector_score", "technical_score", "quant_score",
        "trend_structure_score", "stage_pattern_score", "volume_price_score",
        "momentum_relative_strength_score", "structure_health_score", "sector_rank",
        "return_1d_pct", "return_5d_pct", "return_10d_pct", "return_20d_pct",
        "atr14_pct", "rsi14", "distance_ma20_atr", "close_location", "close_vs_vwap_pct",
        "volume_ratio_1d_prior_10d", "amount_ratio_1d_20d", "data_complete",
        "technical_completeness",
    ]
    available = [column for column in columns if column in frame.columns]
    records = frame.head(limit)[available].to_dict(orient="records")
    for rank, record in enumerate(records, start=1):
        record.update({
            "quant_rank": rank,
            "origin": origin,
            "final_review_pending": True,
            "risk_annotations": [],
            "risk_score_effect": 0,
            "risk_rank_effect": False,
        })
    return records


def compute(target_date: str, previous_date: str, next_date: str) -> dict[str, object]:
    started = time.perf_counter()
    config = load_json(CONFIG_PATH)
    observation_path = ROOT / str(config["universe"]["observation_pool_path"])
    history_path = ROOT / "output/history_daily_latest.csv"
    for path in (CONFIG_PATH, observation_path, history_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    input_hashes = {path.name: sha256(path) for path in (CONFIG_PATH, observation_path, history_path)}

    observation = pd.read_csv(observation_path, dtype={"ts_code": "string", "code": "string"})
    expected = int(config["universe"]["expected_observation_pool_count"])
    if len(observation) != expected or observation["ts_code"].duplicated().any():
        raise ValueError(f"正式观察池必须恰为{expected}只且代码唯一，实际{len(observation)}只")
    observation = observation[["ts_code"]].copy()
    observation_codes = set(observation["ts_code"].astype(str))

    daily_path = ROOT / f"data/history_daily/daily_{target_date}.csv"
    daily, daily_source = data_adapter.fetch_daily_once(target_date, daily_path)
    index_history, index_source = data_adapter.fetch_major_index_history(target_date, ROOT/f"data/v2/index_history_{target_date}.csv")
    metadata_path = ROOT / f"data/v2/stock_metadata_{target_date}.csv"
    base, metadata_source = data_adapter.fetch_market_metadata(target_date, daily, metadata_path)
    required_base = {"ts_code", "name", "industry", "segment", "total_mv_yi", "delisting_risk"}
    if not required_base.issubset(base.columns) or base["ts_code"].duplicated().any():
        raise ValueError(f"市场元数据字段或唯一性异常: {sorted(required_base-set(base.columns))}")
    if not observation_codes.issubset(set(base["ts_code"].astype(str))):
        raise ValueError("观察池存在市场元数据未匹配代码")
    missing_official = observation_codes - set(daily["ts_code"].astype(str))
    base_codes = set(base["ts_code"].astype(str))
    recent, market_dates = data_adapter.load_recent_market_history(ROOT/"data/history_daily", daily, target_date, base_codes)
    light = data_adapter.add_light_features(recent)
    market = market_snapshot(light, index_history, target_date)
    sectors = build_sector_scores(light, base, previous_date, target_date)
    strong = strong_sectors(
        sectors,
        float(config["sector"]["strong_minimum_score"]),
        float(config["sector"]["strong_top_fraction"]),
    )

    official_history, official_guard = load_history(observation_codes, daily, target_date)
    official_features = build_stock_features(official_history, target_date)
    official_meta = observation.merge(base[["ts_code", "name", "industry", "segment"]], on="ts_code", how="left", validate="one_to_one")
    scored_official = score_stocks(
        official_features, official_meta, sectors,
        float(config["quantitative"]["minimum_subscore_completeness"]),
    )
    incomplete_official_codes = sorted(observation_codes-set(scored_official["ts_code"].astype(str)))
    official_eligible = scored_official.loc[scored_official["technical_eligible"]].copy()

    external_config = config["universe"]["external"]
    external_meta = base.loc[
        ~base["ts_code"].astype(str).isin(observation_codes)
        & base["industry"].astype(str).isin(strong)
        & base["segment"].astype(str).isin(external_config["allowed_segments"])
        & pd.to_numeric(base["total_mv_yi"], errors="coerce").ge(float(external_config["minimum_total_market_value_yi"]))
        & ~base["delisting_risk"].fillna(True).astype(bool),
        ["ts_code", "name", "industry", "segment"],
    ].copy()
    external_codes = set(external_meta["ts_code"].astype(str))
    external_guard: dict[str, object] = {"future_data_rows_used": 0, "target_rows_added": 0}
    if external_codes:
        external_history, external_guard = load_history(external_codes, daily, target_date)
        external_features = build_stock_features(external_history, target_date)
        scored_external = score_stocks(
            external_features, external_meta, sectors,
            float(config["quantitative"]["minimum_subscore_completeness"]),
        )
        external_eligible = scored_external.loc[scored_external["technical_eligible"]].copy()
    else:
        external_eligible = pd.DataFrame()

    review_limit = int(config["quantitative"]["review_limit"])
    external_limit = int(external_config["review_limit"])
    official_records = candidate_records(official_eligible, review_limit, "official_pool")
    external_records = candidate_records(external_eligible, external_limit, "external_pool")
    for record in external_records:
        record["activation_rule"] = "仅当终审后同一强势板块无正式池合格个股时方可入池外最终名单"
        record["manual_pool_admission_only"] = True

    payload: dict[str, object] = {
        "schema_version": "2.0.0",
        "model_version": "V2.0",
        "stage": "21:30_quantitative_review_payload",
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "trade_date": target_date,
        "previous_trade_date": previous_date,
        "next_trade_date": next_date,
        "market_environment": market,
        "sector_summary": {
            "sector_count": len(sectors),
            "minimum_sample_count": None,
            "excluded_sectors": [],
            "strong_rule": "score>=9/15 and top30%",
            "strong_sectors": sorted(strong),
            "all_sectors": sectors.to_dict(orient="records"),
        },
        "official_review_candidates": official_records,
        "external_review_candidates": external_records,
        "rules": {
            "weights": {"market_fit": 5, "sector": 15, "technical": 30, "logic_purity": 15, "elasticity": 20, "fundamentals": 10, "major_positive": 5},
            "technical_minimum": 18,
            "quant_review_limit": 30,
            "minimum_final_score": 60,
            "risk_score_effect": 0,
            "risk_rank_effect": False,
        },
        "pool_validation": {
            "official_pool_count": len(observation),
            "official_pool_duplicate_count": int(observation["ts_code"].duplicated().sum()),
            "official_pool_missing_target_daily": len(missing_official),
            "official_pool_data_incomplete_no_formal": incomplete_official_codes,
            "focus_label_columns_present_in_runtime_pool": [],
            "external_scan_population": len(external_meta),
        },
        "data_fetch": {"daily_source": daily_source, "metadata_source": metadata_source, "index_source": index_source, "market_cache_dates": market_dates},
        "future_data_guard": {"official": official_guard, "external": external_guard},
        "validation": {
            "future_data_rows_used": int(official_guard["future_data_rows_used"])+int(external_guard["future_data_rows_used"])+int(index_history["trade_date"].astype(str).gt(target_date).sum()),
            "official_candidates_from_pool": all(item["ts_code"] in observation_codes for item in official_records),
            "official_count_at_most_30": len(official_records) <= 30,
            "external_count_at_most_10": len(external_records) <= 10,
            "all_review_candidates_technical_ge_18": all(float(item["technical_score"]) >= 18 for item in official_records+external_records),
            "risk_changes_score_or_rank": False,
        },
        "input_sha256": input_hashes,
        "elapsed_seconds": round(time.perf_counter()-started, 3),
    }
    validation = payload["validation"]
    if validation["future_data_rows_used"] or not all([
        validation["official_candidates_from_pool"], validation["official_count_at_most_30"],
        validation["external_count_at_most_10"], validation["all_review_candidates_technical_ge_18"],
    ]):
        raise RuntimeError(f"V2.0量化提交前验证失败: {validation}")
    latest = ROOT / str(config["output"]["review_payload_latest"])
    dated = ROOT / f"data/v2/key10_v2_review_payload_{target_date}.json"
    write_json_pair(latest, dated, payload)
    payload["output_files"] = [
        {"path": str(latest), "sha256": sha256(latest)},
        {"path": str(dated), "sha256": sha256(dated)},
    ]
    return payload


def write_status(value: dict[str, object]) -> None:
    STATUS_LATEST.parent.mkdir(parents=True, exist_ok=True)
    from stock_key10_v2_common import atomic_write
    atomic_write(STATUS_LATEST, json_bytes(value))


def main() -> int:
    os.chdir(ROOT)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("V2.0量化任务已有实例运行，本次跳过。")
        return 0
    started = now_bj()
    target_hint = started.strftime("%Y%m%d")
    try:
        calendar = trading_calendar()
        complete = completed_dates(calendar)
        if not complete:
            raise RuntimeError("交易日历窗口内无已完成交易日")
        previous_success = last_success()
        pending = [date for date in complete if previous_success is None or date > previous_success]
        if len(pending) > MAX_CATCH_UP_DAYS:
            raise RuntimeError(f"待补交易日{len(pending)}个，超过安全上限{MAX_CATCH_UP_DAYS}")
        if not pending:
            status = {"schema_version": "2.0.0", "status": "skipped", "reason": "non_trading_day_or_already_completed", "generated_at": now_bj().isoformat(timespec="seconds")}
            write_status(status)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        result: dict[str, object] = {}
        for target in pending:
            target_hint = target
            previous, next_date = adjacent_dates(calendar, target)
            result = compute(target, previous, next_date)
        status = {
            "schema_version": "2.0.0", "status": "success", "trade_date": result["trade_date"],
            "started_at": started.isoformat(timespec="seconds"), "ended_at": now_bj().isoformat(timespec="seconds"),
            "official_review_count": len(result["official_review_candidates"]),
            "external_review_count": len(result["external_review_candidates"]),
            "review_payload": str(ROOT/"output/key10_v2_review_payload_latest.json"),
        }
        write_status(status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        status = {
            "schema_version": "2.0.0", "status": "failed", "trade_date": target_hint,
            "started_at": started.isoformat(timespec="seconds"), "ended_at": now_bj().isoformat(timespec="seconds"),
            "error_summary": safe_error(exc), "previous_success_outputs_preserved": True,
        }
        try:
            write_status(status)
        except Exception:
            pass
        traceback.print_exc()
        return 1
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
