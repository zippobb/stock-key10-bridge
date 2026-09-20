"""V2 adapter for user-confirmed project taxonomy; not a market-membership feed."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

SOURCE_PATH = "theme_assets/stock_theme_tags_229_v2.0.json"
SOURCE_COMMIT = "7828d38c41b06a4caaea5f04beeb0210cb08aec5"
SOURCE_BLOB_SHA = "fafa071ae49e54e34e32a6f2f7313caf5e797fd8"
MAPPING_SHA256 = "65de11ec333dd470fe66fa52964405aa1731e331986647a087e796db7b8ef39c"
SCHEMA_VERSION = "1.0.0"
CLASSIFICATION_VERSION = "2.0"
RULES_VERSION = "1.2"
MEMBER_COUNT = 229
THEME_COUNT = 88
CODE_PATTERN = re.compile(r"^\d{6}\.(?:SH|SZ)$")
BUSINESS_STATUS = "not_independently_verified_by_this_import"
NOT_RECALL_REASON = (
    "User-confirmed project classification is not a verified membership feed; "
    "the source has no source_url, observed_at, or historical effective_from."
)


def _unavailable(errors: list[str], pool_codes: set[str]) -> dict:
    return {
        "mapping": {},
        "source_metadata": {
            "source_path": SOURCE_PATH,
            "source_commit": SOURCE_COMMIT,
            "source_blob_sha": SOURCE_BLOB_SHA,
            "mapping_sha256": MAPPING_SHA256,
            "classification_version": CLASSIFICATION_VERSION,
            "rules_version": RULES_VERSION,
            "imported_at": None,
        },
        "diagnostics": {
            "status": "unavailable",
            "errors": errors,
            "scope": "229_user_pool_members_only",
            "market_wide_breadth": False,
            "mapping_member_count": 0,
            "distinct_primary_theme_count": 0,
            "pool_count": len(pool_codes),
            "mapped_pool_count": 0,
            "unmapped_pool_count": len(pool_codes),
            "unmapped_pool_codes": sorted(pool_codes),
            "mapping_codes_outside_pool": [],
            "formal_theme_recall_enabled": False,
            "formal_theme_recall_eligible_members": 0,
            "formal_theme_recall_rejected_members": 0,
            "formal_theme_recall_rejection_reason": NOT_RECALL_REASON,
        },
    }


def load_theme_classification(
    mapping_path: str | Path,
    import_metadata_path: str | Path,
    production_pool_codes: set[str],
) -> dict:
    """Load a pinned taxonomy, failing closed to no labels with explicit diagnostics."""
    pool_codes = {str(code) for code in production_pool_codes}
    mapping_path = Path(mapping_path)
    import_metadata_path = Path(import_metadata_path)
    errors: list[str] = []
    try:
        raw = mapping_path.read_bytes()
    except OSError as exc:
        return _unavailable([f"mapping_file_unavailable:{type(exc).__name__}"], pool_codes)
    try:
        metadata = json.loads(import_metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _unavailable([f"import_metadata_unavailable_or_invalid:{type(exc).__name__}"], pool_codes)
    if not isinstance(metadata, dict):
        return _unavailable(["import_metadata_top_level_not_object"], pool_codes)

    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != MAPPING_SHA256:
        errors.append("mapping_sha256_mismatch")
    expected_metadata = {
        "source_path": SOURCE_PATH,
        "source_commit": SOURCE_COMMIT,
        "source_blob_sha": SOURCE_BLOB_SHA,
        "mapping_sha256": MAPPING_SHA256,
        "classification_version": CLASSIFICATION_VERSION,
        "rules_version": RULES_VERSION,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            errors.append(f"import_metadata_{key}_mismatch")
    imported_at = metadata.get("imported_at")
    try:
        datetime.fromisoformat(str(imported_at))
    except (TypeError, ValueError):
        errors.append("imported_at_invalid")

    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        return _unavailable(errors + [f"mapping_json_invalid:{type(exc).__name__}"], pool_codes)
    if not isinstance(document, dict):
        return _unavailable(errors + ["mapping_json_top_level_not_object"], pool_codes)

    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version_mismatch")
    if document.get("classification_version") != CLASSIFICATION_VERSION:
        errors.append("classification_version_mismatch")
    if document.get("rules_version") != RULES_VERSION:
        errors.append("rules_version_mismatch")
    if document.get("source") != "User-confirmed project classification; not a verified market concept feed":
        errors.append("source_type_mismatch")
    if document.get("historical_effective_date_known") is not False:
        errors.append("historical_effective_date_claim_mismatch")
    if document.get("coverage_scope") != "229 user pool members only":
        errors.append("coverage_scope_mismatch")

    members = document.get("members")
    if not isinstance(members, list) or len(members) != MEMBER_COUNT:
        errors.append("member_count_mismatch")
        members = []
    codes = [str(item.get("ts_code", "")) for item in members if isinstance(item, dict)]
    if len(codes) != len(members) or len(set(codes)) != len(codes):
        errors.append("duplicate_or_missing_ts_code")
    if any(not CODE_PATTERN.fullmatch(code) for code in codes):
        errors.append("invalid_ts_code")
    if len({item.get("primary_theme") for item in members if isinstance(item, dict)}) != THEME_COUNT:
        errors.append("primary_theme_count_mismatch")

    normalized: dict[str, dict] = {}
    for item in members:
        if not isinstance(item, dict):
            continue
        code = str(item.get("ts_code", ""))
        if (
            not isinstance(item.get("primary_theme"), str)
            or not item.get("primary_theme")
            or item.get("classification_status") != "user_confirmed"
            or item.get("business_verification_status") != BUSINESS_STATUS
            or item.get("effective_from") is not None
            or not isinstance(item.get("business_realization"), str)
            or not isinstance(item.get("fact_summary"), str)
            or item.get("verified") is True
        ):
            errors.append(f"member_semantics_invalid:{code or 'missing_code'}")
            continue
        normalized[code] = {
            "primary_theme": item["primary_theme"],
            "business_realization": item["business_realization"],
            "fact_summary": item["fact_summary"],
            "classification_status": item["classification_status"],
            "business_verification_status": item["business_verification_status"],
            "effective_from": None,
            "source_published_at": None,
            "observed_at": None,
        }

    if errors:
        return _unavailable(sorted(set(errors)), pool_codes)

    mapping_codes = set(normalized)
    mapped_pool_codes = pool_codes & mapping_codes
    outside = mapping_codes - pool_codes
    missing = pool_codes - mapping_codes
    source_metadata = {
        **expected_metadata,
        "imported_at": imported_at,
    }
    diagnostics = {
        "status": "loaded" if not missing and not outside else "loaded_pool_mismatch",
        "errors": [],
        "scope": "229_user_pool_members_only",
        "market_wide_breadth": False,
        "mapping_member_count": len(normalized),
        "distinct_primary_theme_count": len({item["primary_theme"] for item in normalized.values()}),
        "pool_count": len(pool_codes),
        "mapped_pool_count": len(mapped_pool_codes),
        "unmapped_pool_count": len(missing),
        "unmapped_pool_codes": sorted(missing),
        "mapping_codes_outside_pool": sorted(outside),
        "formal_theme_recall_enabled": False,
        "formal_theme_recall_eligible_members": 0,
        "formal_theme_recall_rejected_members": len(normalized),
        "formal_theme_recall_rejection_reason": NOT_RECALL_REASON,
    }
    return {
        "mapping": normalized,
        "source_metadata": source_metadata,
        "diagnostics": diagnostics,
    }


def _candidate_classification(code: str, snapshot: dict) -> dict:
    item = snapshot["mapping"].get(str(code))
    metadata = snapshot["source_metadata"]
    if item is None:
        return {
            "primary_theme": None,
            "business_realization": None,
            "fact_summary": None,
            "classification_status": (
                "classification_unavailable"
                if snapshot["diagnostics"]["status"] == "unavailable"
                else "not_in_mapping"
            ),
            "business_verification_status": (
                "classification_source_unavailable"
                if snapshot["diagnostics"]["status"] == "unavailable"
                else "not_assessed_outside_229_mapping"
            ),
            "effective_from": None,
            "source_published_at": None,
            "observed_at": None,
            "classification_version": metadata["classification_version"],
            "rules_version": metadata["rules_version"],
            "source_path": metadata["source_path"],
            "source_commit": metadata["source_commit"],
            "source_blob_sha": metadata["source_blob_sha"],
            "imported_at": metadata["imported_at"],
            "usage_scope": "classification_only_not_recall_membership",
        }
    return {
        **item,
        "classification_version": metadata["classification_version"],
        "rules_version": metadata["rules_version"],
        "source_path": metadata["source_path"],
        "source_commit": metadata["source_commit"],
        "source_blob_sha": metadata["source_blob_sha"],
        "imported_at": metadata["imported_at"],
        "usage_scope": "classification_only_not_recall_membership",
    }


def enrich_candidate_records(records: list[dict], snapshot: dict) -> list[dict]:
    """Copy records and add display/diagnostic classification without modifying scores."""
    result = []
    for record in records:
        item = dict(record)
        item["theme_classification"] = _candidate_classification(
            str(item.get("ts_code", "")), snapshot,
        )
        result.append(item)
    return result


def _theme_counts(codes, mapping: dict[str, dict]) -> dict[str, int]:
    counts = Counter(
        mapping[code]["primary_theme"]
        for code in {str(item) for item in codes}
        if code in mapping
    )
    return dict(sorted(counts.items()))


def build_theme_classification_diagnostics(
    snapshot: dict,
    production_pool_codes: set[str],
    official_scanned_codes,
    official_eligible_codes,
    external_scanned_codes,
    official_records: list[dict],
    external_records: list[dict],
) -> dict:
    mapping = snapshot["mapping"]
    base = dict(snapshot["diagnostics"])
    official_scanned = {str(code) for code in official_scanned_codes}
    official_eligible = {str(code) for code in official_eligible_codes}
    external_scanned = {str(code) for code in external_scanned_codes}
    official_candidate_codes = [str(item.get("ts_code", "")) for item in official_records]
    external_candidate_codes = [str(item.get("ts_code", "")) for item in external_records]
    base.update({
        "source_type": "user_confirmed_project_classification",
        "source": snapshot["source_metadata"]["source_path"],
        "source_commit": snapshot["source_metadata"]["source_commit"],
        "source_blob_sha": snapshot["source_metadata"]["source_blob_sha"],
        "mapping_sha256": snapshot["source_metadata"]["mapping_sha256"],
        "imported_at": snapshot["source_metadata"]["imported_at"],
        "classification_version": snapshot["source_metadata"]["classification_version"],
        "rules_version": snapshot["source_metadata"]["rules_version"],
        "production_pool_code_set_match": (
            set(str(code) for code in production_pool_codes) == set(mapping)
        ) if mapping else False,
        "scan_coverage": {
            "official": {
                "scanned_rows": len(official_scanned),
                "mapped_rows": len(official_scanned & set(mapping)),
                "unmapped_rows": len(official_scanned - set(mapping)),
                "eligible_rows": len(official_eligible),
                "mapped_eligible_rows": len(official_eligible & set(mapping)),
                "primary_theme_counts_all_scanned": _theme_counts(official_scanned, mapping),
                "primary_theme_counts_eligible": _theme_counts(official_eligible, mapping),
            },
            "external": {
                "scanned_rows": len(external_scanned),
                "mapped_rows": len(external_scanned & set(mapping)),
                "unmapped_rows": len(external_scanned - set(mapping)),
            },
        },
        "candidate_coverage": {
            "official": {
                "candidate_count": len(official_records),
                "classified_count": sum(code in mapping for code in official_candidate_codes),
                "pending_classification_count": sum(code not in mapping for code in official_candidate_codes),
                "primary_theme_counts": _theme_counts(official_candidate_codes, mapping),
            },
            "external": {
                "candidate_count": len(external_records),
                "classified_count": sum(code in mapping for code in external_candidate_codes),
                "pending_classification_count": sum(code not in mapping for code in external_candidate_codes),
                "unclassified_candidate_codes": sorted(code for code in external_candidate_codes if code not in mapping),
            },
        },
        "candidate_labels_are_diagnostic_only": True,
        "classification_import_is_not_business_verification": True,
        "formal_recall_rejection_reason": NOT_RECALL_REASON,
    })
    return base
