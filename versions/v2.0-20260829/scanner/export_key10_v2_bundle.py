#!/usr/bin/env python3
"""Publish only the validated V2.0 21:30 review bundle for the 22:00 task."""

from __future__ import annotations

import grp
import os
from pathlib import Path

from stock_key10_v2_common import atomic_write, forbidden_keys, load_json, sha256


ROOT = Path(os.environ.get("STOCK_AUTOMATION_ROOT", "/opt/stock-automation/scanner"))
SOURCE = ROOT / "output/key10_v2_review_payload_latest.json"
DESTINATION = Path(os.environ.get("STOCK_V2_BUNDLE_PATH", "/var/lib/stock-key10-mcp/key10_bundle_latest.json"))
MAX_BYTES = 1024 * 1024
FORBIDDEN = {
    "is_focus", "focus_rank", "risk_status", "risk_priority", "veto", "pass_warning_fail",
    "market_hard_gate", "hard_gate", "market_veto",
}


def validate(value: dict[str, object]) -> None:
    if value.get("schema_version") != "2.0.0" or value.get("model_version") != "V2.0":
        raise ValueError("拒绝发布非V2.0量化包")
    if value.get("stage") != "21:30_quantitative_review_payload":
        raise ValueError("拒绝发布非21:30复核阶段数据")
    validation = value.get("validation", {})
    if validation.get("future_data_rows_used") != 0:
        raise ValueError("量化包包含未来数据")
    if validation.get("risk_changes_score_or_rank") is not False:
        raise ValueError("量化包风险规则不符合只标记原则")
    official = value.get("official_review_candidates", [])
    external = value.get("external_review_candidates", [])
    if len(official) > 30 or len(external) > 10:
        raise ValueError("量化包候选数量超过上限")
    if any(float(item.get("technical_score", -1)) < 18 for item in official+external):
        raise ValueError("量化包含技术分低于18的候选")
    found = forbidden_keys(value, FORBIDDEN)
    if found:
        raise ValueError(f"量化包含V2.0禁用字段: {sorted(found)}")


def main() -> int:
    value = load_json(SOURCE)
    validate(value)
    payload = SOURCE.read_bytes()
    if len(payload) > MAX_BYTES:
        raise ValueError(f"量化包超过1MiB: {len(payload)}")
    atomic_write(DESTINATION, payload)
    os.chmod(DESTINATION, 0o640)
    try:
        group = grp.getgrnam("stockpublisher")
        os.chown(DESTINATION, -1, group.gr_gid)
    except KeyError:
        pass
    if sha256(SOURCE) != sha256(DESTINATION):
        raise RuntimeError("发布包哈希验证失败")
    print(f"V2.0量化包已发布: {DESTINATION} ({len(payload)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
