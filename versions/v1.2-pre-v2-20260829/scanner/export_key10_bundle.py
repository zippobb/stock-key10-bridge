#!/usr/bin/env python3
"""Export a validated, credential-free, read-only Key10 result bundle."""

from __future__ import annotations

import csv
import grp
import hashlib
import ipaddress
import json
import math
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path("/opt/stock-automation/scanner")
OUTPUT = ROOT / "output"
BUNDLE_DIR = Path("/var/lib/stock-key10-mcp")
MAX_BYTES = 1024 * 1024
DATE_RE = re.compile(r"^[0-9]{8}$")
SECRET_RE = <REDACTED>
    r"(?i)(tushare[_ -]?token|access[_ -]?key|secret[_ -]?access|mcp[_ -]?api[_ -]?key|openai[_ -]?api[_ -]?key|api[_ -]?secret)"
)

SOURCES = {
    "automation_status": OUTPUT / "automation_status_latest.json",
    "technical_shortlist": OUTPUT / "key10_technical_shortlist_latest.csv",
    "near_signal_candidates": OUTPUT / "key10_near_signal_watch_latest.csv",
    "market_environment": OUTPUT / "key10_market_environment_latest.json",
    "market_risk": OUTPUT / "market_risk_warning_latest.json",
    "sector_rotation": OUTPUT / "sector_rotation_latest.json",
    "daily_diagnostics": OUTPUT / "key10_daily_diagnostics_latest.json",
}
OPTIONAL_REVIEW = OUTPUT / "key10_review_latest.csv"
FORMAL_FUSION = OUTPUT / "key10_v12_fusion_latest.json"


def now_bj() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_fixed_source(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"拒绝符号链接输入: {path.name}")
    resolved = path.resolve(strict=True)
    if resolved.parent != OUTPUT.resolve(strict=True):
        raise RuntimeError(f"输入不在固定结果目录: {path.name}")
    if not resolved.is_file():
        raise FileNotFoundError(f"结果文件不存在: {path.name}")


def load_json(path: Path) -> dict[str, object]:
    check_fixed_source(path)
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"非标准UTF-8 JSON: {path.name}") from exc
    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON顶层不是对象: {path.name}")
    return value


def coerce_csv_value(value: str | None) -> object:
    if value is None or value == "" or value.lower() in {"nan", "none", "null"}:
        return None
    if value in {"True", "False", "true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?[0-9]+", value) and not (len(value) > 1 and value.startswith("0")):
        try:
            return int(value)
        except ValueError:
            pass
    if re.fullmatch(r"-?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except ValueError:
            pass
    return value


def load_csv(path: Path) -> list[dict[str, object]]:
    check_fixed_source(path)
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
        with handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise RuntimeError(f"CSV没有字段: {path.name}")
            return [{key: coerce_csv_value(value) for key, value in row.items()} for row in reader]
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"非标准UTF-8 CSV: {path.name}") from exc


def row_dates(rows: list[dict[str, object]]) -> set[str]:
    return {str(row.get("trade_date", "")) for row in rows}


def expected_hashes(status: dict[str, object]) -> dict[str, str]:
    expected: dict[str, str] = {}
    output_files = status.get("output_files", [])
    if not isinstance(output_files, list):
        raise RuntimeError("automation_status.output_files格式错误")
    for item in output_files:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
            expected[Path(item["path"]).name] = item["sha256"]
    return expected


def validate_source_hashes(status: dict[str, object]) -> dict[str, str]:
    expected = expected_hashes(status)
    actual = {name: sha256_file(path) for name, path in SOURCES.items()}
    for name in ["technical_shortlist", "near_signal_candidates", "market_environment", "market_risk", "sector_rotation", "daily_diagnostics"]:
        filename = SOURCES[name].name
        wanted = expected.get(filename)
        if not wanted:
            raise RuntimeError(f"automation_status缺少源文件SHA256: {filename}")
        if actual[name] != wanted:
            raise RuntimeError(f"源文件SHA256异常: {filename}")
    if OPTIONAL_REVIEW.exists():
        check_fixed_source(OPTIONAL_REVIEW)
        actual["previous_review"] = sha256_file(OPTIONAL_REVIEW)
    return actual


def sanitize_status(status: dict[str, object]) -> dict[str, object]:
    allowed = [
        "status", "started_at", "ended_at", "exit_code", "elapsed_seconds", "target_trade_date",
        "previous_trade_date", "next_trade_date", "formal_candidate_count", "near_signal_count",
        "market_state", "market_risk_level", "market_score_raw", "market_score_3d_smoothed",
        "market_bottom_status", "processed_trade_dates", "error_summary",
    ]
    return {key: status.get(key) for key in allowed}


def load_formal_fusion(
    trade_date: str,
    source_hashes: dict[str, str],
    shortlist: list[dict[str, object]],
    near: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]], list[dict[str, object]], str]:
    """Validate the mandatory formal V1.2 fusion result and its V1.0 lineage."""
    fusion = load_json(FORMAL_FUSION)
    if not str(fusion.get("schema_version", "")).startswith("1.2-fusion-") or fusion.get("layer_status") != "formal_fusion":
        raise RuntimeError("V1.2正式融合schema或状态无效")
    if str(fusion.get("trade_date", "")) != trade_date:
        raise RuntimeError("V1.2正式融合交易日不一致")
    if fusion.get("pool_changed") is not False or fusion.get("v1_0_formula_changed") is not False:
        raise RuntimeError("V1.2结果声明修改了股票池或V1.0公式")
    if int(fusion.get("future_data_rows", -1)) != 0:
        raise RuntimeError("V1.2结果future_data_rows非零")
    recommendations = fusion.get("recommendations")
    alternates = fusion.get("alternates")
    if not isinstance(recommendations, list) or not isinstance(alternates, list):
        raise RuntimeError("V1.2推荐或备选结构无效")
    published = recommendations + alternates
    formal_output_count = len(published)
    if formal_output_count > 10:
        raise RuntimeError("V1.2正式输出超过10只硬上限")
    if int(fusion.get("formal_output_count", -1)) != formal_output_count:
        raise RuntimeError("V1.2 formal_output_count与实际数量不一致")
    codes = [str(row.get("ts_code")) for row in published if isinstance(row, dict)]
    ranks = [row.get("fusion_rank") for row in published if isinstance(row, dict)]
    if len(codes) != len(published) or len(codes) != len(set(codes)) or len(ranks) != len(set(ranks)):
        raise RuntimeError("V1.2推荐/备选代码或排名不唯一")
    if any(str(row.get("v1_1_risk_status")) == "FAIL" for row in published):
        raise RuntimeError("V1.2正式输出意外包含FAIL")
    fusion_sources = fusion.get("source_file_sha256", {})
    if not isinstance(fusion_sources, dict):
        raise RuntimeError("V1.2源文件哈希结构无效")
    if fusion_sources.get("v1_0_formal") != source_hashes.get("technical_shortlist"):
        raise RuntimeError("V1.2正式候选源哈希不匹配")
    if fusion_sources.get("v1_0_near") != source_hashes.get("near_signal_candidates"):
        raise RuntimeError("V1.2近似候选源哈希不匹配")
    fusion_sha = sha256_file(FORMAL_FUSION)
    return fusion, {"status": "valid", "reason": None, "sha256": fusion_sha}, recommendations, alternates, fusion_sha


def reject_sensitive_content(text: str) -> None:
    if SECRET_RE.search(text):
        raise RuntimeError("清洗结果检测到凭证关键词，拒绝导出")
    for token in re.findall(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])", text):
        try:
            address = ipaddress.ip_address(token)
        except ValueError:
            continue
        if not (address.is_private or address.is_loopback or address.is_unspecified):
            raise RuntimeError("清洗结果检测到公网IP，拒绝导出")


def bundle_payload(bundle: dict[str, object]) -> bytes:
    return json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"


def enforce_permissions(path: Path, gid: int) -> None:
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)


def backup_if_different(path: Path, payload: bytes, stamp: str) -> Path | None:
    if not path.is_file() or path.read_bytes() == payload:
        return None
    backup = path.with_name(f"{path.name}.bak.STEP6B1_{stamp}")
    shutil.copy2(path, backup)
    return backup


def atomic_commit(paths: list[Path], payload: bytes, gid: int) -> list[str]:
    stamp = now_bj().strftime("%Y%m%d_%H%M%S_%f")
    backups = {path: backup_if_different(path, payload, stamp) for path in paths}
    staged: dict[Path, Path] = {}
    changed: list[Path] = []
    for path in paths:
        if path.is_file() and path.read_bytes() == payload:
            enforce_permissions(path, gid)
            continue
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=BUNDLE_DIR)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path = Path(temp_name)
        enforce_permissions(temp_path, gid)
        staged[path] = temp_path
    try:
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
            changed.append(path)
    except Exception:
        for path in changed:
            failed = path.with_name(f"{path.name}.failed.STEP6B1_{stamp}")
            os.replace(path, failed)
            backup = backups.get(path)
            if backup is not None:
                shutil.copy2(backup, path)
        for path, temp_path in staged.items():
            if temp_path.exists():
                failed = path.with_name(f"{path.name}.failed.STEP6B1_{stamp}")
                os.replace(temp_path, failed)
        raise
    return [str(path) for path in backups.values() if path is not None]


def validate_existing_bundle_for_skip() -> None:
    latest = BUNDLE_DIR / "key10_bundle_latest.json"
    if not latest.is_file() or latest.is_symlink():
        raise RuntimeError("非交易日跳过时不存在上一份成功结果包")
    data = json.loads(latest.read_text(encoding="utf-8"))
    date = str(data.get("trade_date", ""))
    if not DATE_RE.fullmatch(date):
        raise RuntimeError("上一份成功结果包交易日无效")
    dated = BUNDLE_DIR / f"key10_bundle_{date}.json"
    if not dated.is_file() or latest.read_bytes() != dated.read_bytes():
        raise RuntimeError("上一份成功结果包latest与日期版不一致")
    print(f"automation_status=skipped，保留且验证上一份成功结果包: {date}")


def main() -> int:
    status = load_json(SOURCES["automation_status"])
    state = status.get("status")
    if state == "skipped":
        validate_existing_bundle_for_skip()
        return 0
    if state != "success" or status.get("exit_code") != 0:
        raise RuntimeError(f"automation_status不是成功状态: status={state!r}")

    trade_date = str(status.get("target_trade_date", ""))
    previous_date = str(status.get("previous_trade_date", ""))
    next_date = str(status.get("next_trade_date", ""))
    for label, value in [("trade_date", trade_date), ("previous_trade_date", previous_date), ("next_trade_date", next_date)]:
        if not DATE_RE.fullmatch(value):
            raise RuntimeError(f"{label}不是8位YYYYMMDD")

    source_hashes = validate_source_hashes(status)
    shortlist = load_csv(SOURCES["technical_shortlist"])
    near = load_csv(SOURCES["near_signal_candidates"])
    market = load_json(SOURCES["market_environment"])
    risk = load_json(SOURCES["market_risk"])
    sectors = load_json(SOURCES["sector_rotation"])
    diagnostics = load_json(SOURCES["daily_diagnostics"])
    fusion, fusion_status, recommendations, alternates, fusion_sha = load_formal_fusion(trade_date, source_hashes, shortlist, near)
    bundle_source_hashes = dict(source_hashes)
    bundle_source_hashes["v1_2_fusion"] = fusion_sha

    checks = {
        "automation_status": str(status.get("target_trade_date", "")),
        "technical_shortlist": next(iter(row_dates(shortlist)), "") if len(row_dates(shortlist)) == 1 else "MULTIPLE",
        "near_signal_candidates": next(iter(row_dates(near)), "") if len(row_dates(near)) == 1 else "MULTIPLE",
        "market_environment": str(market.get("trade_date", "")),
        "market_risk": str(risk.get("trade_date", "")),
        "sector_rotation": str(sectors.get("trade_date", "")),
        "daily_diagnostics": str(diagnostics.get("target_trade_date", "")),
    }
    mismatched = {name: date for name, date in checks.items() if date != trade_date}
    if mismatched:
        raise RuntimeError(f"结果交易日不一致: {mismatched}")
    if int(status.get("formal_candidate_count", -1)) != len(shortlist):
        raise RuntimeError("正式候选数量与automation_status不一致")
    if int(status.get("near_signal_count", -1)) != len(near):
        raise RuntimeError("近似观察层数量与automation_status不一致")
    if len({str(row.get("ts_code")) for row in shortlist}) != len(shortlist):
        raise RuntimeError("正式候选存在重复ts_code")
    if len({str(row.get("ts_code")) for row in near}) != len(near):
        raise RuntimeError("近似观察层存在重复ts_code")

    previous_review: list[dict[str, object]] | None = None
    review_status = "not_found"
    if OPTIONAL_REVIEW.is_file():
        review = load_csv(OPTIONAL_REVIEW)
        if review and row_dates(review) == {previous_date}:
            previous_review = review
            review_status = "included"
        else:
            review_status = "present_but_not_previous_trade_date"

    validation = diagnostics.get("validation", {})
    future_breakdown = {
        "history": int(validation.get("future_data_rows_used", 0)),
        "index": int(validation.get("index_future_rows_used", 0)),
        "source": int(diagnostics.get("future_data_guard", {}).get("future_rows_used", 0)),
    }
    future_total = sum(future_breakdown.values())
    if future_total != 0:
        raise RuntimeError(f"检测到未来数据引用: {future_breakdown}")

    existing_dated = BUNDLE_DIR / f"key10_bundle_{trade_date}.json"
    if existing_dated.is_file() and not existing_dated.is_symlink():
        existing_raw = existing_dated.read_bytes()
        if len(existing_raw) > MAX_BYTES:
            raise RuntimeError("已有日期结果包超过1MiB")
        existing = json.loads(existing_raw.decode("utf-8"))
        if (
            existing.get("trade_date") == trade_date
            and existing.get("source_file_sha256") == bundle_source_hashes
            and existing.get("future_data_rows", {}).get("total") == 0
            and existing.get("data_quality", {}).get("formal_candidate_count") == len(recommendations)
            and existing.get("data_quality", {}).get("alternate_candidate_count") == len(alternates)
            and existing.get("v1_2_fusion_status") == fusion_status
        ):
            reject_sensitive_content(existing_raw.decode("utf-8"))
            group = grp.getgrnam("stockmcp")
            BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
            os.chown(BUNDLE_DIR, 0, group.gr_gid)
            os.chmod(BUNDLE_DIR, 0o750)
            latest = BUNDLE_DIR / "key10_bundle_latest.json"
            backups = atomic_commit([latest, existing_dated], existing_raw, group.gr_gid)
            print(json.dumps({
                "status": "success_idempotent", "trade_date": trade_date, "bytes": len(existing_raw),
                "formal_candidates": len(recommendations), "alternate_candidates": len(alternates),
                "future_data_rows": 0, "bundle_sha256": existing.get("bundle_sha256"),
                "file_sha256": sha256_file(latest), "backups_created": backups,
            }, ensure_ascii=False, indent=2))
            return 0

    freshness = "fresh" if now_bj().strftime("%Y%m%d") <= next_date else "stale_pending_update"
    warnings = risk.get("important_prior_close_warnings")
    if not warnings:
        warning = risk.get("important_warning")
        warnings = [warning] if warning else []
    rotation_alerts = sectors.get("important_rotation_changes", {})
    bundle: dict[str, object] = {
        "schema_version": "1.2.0",
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "previous_trade_date": previous_date,
        "next_trade_date": next_date,
        "freshness_status": freshness,
        "automation_status": sanitize_status(status),
        "market_environment": market,
        "market_risk": risk,
        "market_repair_status": market.get("market_repair_status"),
        "market_warning": warnings,
        "sector_rotation": sectors,
        "sector_rotation_alerts": rotation_alerts,
        "technical_shortlist": recommendations,
        "near_signal_candidates": alternates,
        "previous_review": previous_review,
        "source_file_sha256": bundle_source_hashes,
        "bundle_sha256": None,
        "bundle_sha256_definition": "SHA256 of canonical UTF-8 JSON with bundle_sha256 set to null",
        "data_quality": {
            "date_consistency": checks,
            "source_sha256_verified": True,
            "formal_candidate_count": len(recommendations),
            "alternate_candidate_count": len(alternates),
            "v1_0_formal_source_count": len(shortlist),
            "v1_0_near_source_count": len(near),
            "formal_duplicate_ts_code": 0,
            "near_duplicate_ts_code": 0,
            "previous_review_status": review_status,
            "v1_2_fusion_status": fusion_status["status"],
            "latest_and_dated_expected_identical": True,
        },
        "future_data_rows": {"total": future_total, **future_breakdown},
        "technical_rules_changed": False,
        "v1_0_technical_rules_changed": False,
        "formal_fusion_active": True,
        "pool_changed": False,
        "v1_2_fusion_status": fusion_status,
        "v1_2_fusion": fusion,
    }
    canonical = bundle_payload(bundle)
    bundle["bundle_sha256"] = sha256_bytes(canonical)
    payload = bundle_payload(bundle)
    if len(payload) > MAX_BYTES:
        raise RuntimeError(f"结果包超过1MiB: {len(payload)} bytes")
    text = payload.decode("utf-8")
    reject_sensitive_content(text)

    group = grp.getgrnam("stockmcp")
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(BUNDLE_DIR, 0, group.gr_gid)
    os.chmod(BUNDLE_DIR, 0o750)
    latest = BUNDLE_DIR / "key10_bundle_latest.json"
    dated = BUNDLE_DIR / f"key10_bundle_{trade_date}.json"
    backups = atomic_commit([latest, dated], payload, group.gr_gid)
    if latest.read_bytes() != dated.read_bytes():
        raise RuntimeError("结果包latest与日期版不一致")
    print(json.dumps({
        "status": "success", "trade_date": trade_date, "bytes": len(payload),
        "formal_candidates": len(recommendations), "alternate_candidates": len(alternates),
        "future_data_rows": future_total, "bundle_sha256": bundle["bundle_sha256"],
        "file_sha256": sha256_file(latest), "backups_created": backups,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
