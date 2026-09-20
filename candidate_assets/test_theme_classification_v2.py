import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import theme_classification_v2 as theme


def fixture_files(root):
    members = []
    for index in range(theme.MEMBER_COUNT):
        members.append({
            "ts_code": f"{100000 + index:06d}.SZ",
            "name": f"样例{index}",
            "primary_theme": f"题材{index % theme.THEME_COUNT:02d}",
            "business_realization": "待核验",
            "fact_summary": "用户确认的项目交易分类",
            "classification_status": "user_confirmed",
            "business_verification_status": theme.BUSINESS_STATUS,
            "effective_from": None,
        })
    document = {
        "schema_version": theme.SCHEMA_VERSION,
        "classification_version": theme.CLASSIFICATION_VERSION,
        "rules_version": theme.RULES_VERSION,
        "source": "User-confirmed project classification; not a verified market concept feed",
        "historical_effective_date_known": False,
        "coverage_scope": "229 user pool members only",
        "members": members,
    }
    mapping_path = root / "mapping.json"
    raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    mapping_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    metadata = {
        "source_path": theme.SOURCE_PATH,
        "source_commit": "test-commit",
        "source_blob_sha": "test-blob",
        "mapping_sha256": digest,
        "classification_version": theme.CLASSIFICATION_VERSION,
        "rules_version": theme.RULES_VERSION,
        "imported_at": "2026-09-20T23:40:00+08:00",
    }
    metadata_path = root / "import.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return mapping_path, metadata_path, members, digest


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mapping_path, self.metadata_path, self.members, digest = fixture_files(self.root)
        self.pool_codes = {item["ts_code"] for item in self.members}
        self.source_patches = patch.multiple(
            theme,
            SOURCE_COMMIT="test-commit",
            SOURCE_BLOB_SHA="test-blob",
            MAPPING_SHA256=digest,
        )
        self.source_patches.start()
        self.addCleanup(self.source_patches.stop)

    def test_current_user_map_loads_without_becoming_recall_evidence(self):
        snapshot = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        self.assertEqual(snapshot["diagnostics"]["status"], "loaded")
        self.assertEqual(len(snapshot["mapping"]), 229)
        self.assertEqual(snapshot["diagnostics"]["distinct_primary_theme_count"], 88)
        self.assertFalse(snapshot["diagnostics"]["formal_theme_recall_enabled"])
        self.assertEqual(snapshot["diagnostics"]["formal_theme_recall_eligible_members"], 0)
        self.assertEqual(snapshot["diagnostics"]["formal_theme_recall_rejected_members"], 229)

    def test_pool_mismatch_is_explicit_and_does_not_hide_codes(self):
        pool = set(self.pool_codes)
        pool.remove("100000.SZ")
        pool.add("999999.SH")
        snapshot = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, pool,
        )
        self.assertEqual(snapshot["diagnostics"]["status"], "loaded_pool_mismatch")
        self.assertEqual(snapshot["diagnostics"]["mapped_pool_count"], 228)
        self.assertEqual(snapshot["diagnostics"]["unmapped_pool_codes"], ["999999.SH"])
        self.assertEqual(snapshot["diagnostics"]["mapping_codes_outside_pool"], ["100000.SZ"])

    def test_missing_or_bad_file_fails_closed_with_reason(self):
        missing = theme.load_theme_classification(
            self.root / "missing.json", self.metadata_path, self.pool_codes,
        )
        self.assertEqual(missing["mapping"], {})
        self.assertIn("mapping_file_unavailable:FileNotFoundError", missing["diagnostics"]["errors"][0])

        self.mapping_path.write_text("{bad json", encoding="utf-8")
        bad = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        self.assertEqual(bad["mapping"], {})
        self.assertIn("mapping_sha256_mismatch", bad["diagnostics"]["errors"])
        self.assertTrue(any(item.startswith("mapping_json_invalid:") for item in bad["diagnostics"]["errors"]))

    def test_valid_json_with_wrong_top_level_shape_fails_closed(self):
        self.metadata_path.write_text("[]", encoding="utf-8")
        bad_metadata = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        self.assertEqual(bad_metadata["mapping"], {})
        self.assertIn("import_metadata_top_level_not_object", bad_metadata["diagnostics"]["errors"])

        _, self.metadata_path, _, _ = fixture_files(self.root)
        self.mapping_path.write_text("[]", encoding="utf-8")
        bad_mapping = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        self.assertEqual(bad_mapping["mapping"], {})
        self.assertIn("mapping_json_top_level_not_object", bad_mapping["diagnostics"]["errors"])

    def test_candidate_metadata_is_separate_and_input_is_not_mutated(self):
        snapshot = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        original = [{
            "ts_code": "100000.SZ",
            "quant_score": 82.5,
            "kdj_k": 51.0,
            "origin": "official_pool",
            "candidate_source": {"routes": ["early_leader"]},
        }]
        before = json.loads(json.dumps(original))
        output = theme.enrich_candidate_records(original, snapshot)
        cls = output[0]["theme_classification"]
        self.assertEqual(cls["classification_status"], "user_confirmed")
        self.assertEqual(cls["business_verification_status"], theme.BUSINESS_STATUS)
        self.assertIsNone(cls["effective_from"])
        self.assertIsNone(cls["source_published_at"])
        self.assertIsNone(cls["observed_at"])
        self.assertEqual(cls["usage_scope"], "classification_only_not_recall_membership")
        self.assertNotIn("verified", cls)
        self.assertNotIn("source_url", cls)
        self.assertEqual(output[0]["quant_score"], 82.5)
        self.assertEqual(output[0]["kdj_k"], 51.0)
        self.assertEqual(original, before)

    def test_external_candidate_remains_pending_when_outside_mapping(self):
        snapshot = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        output = theme.enrich_candidate_records([{
            "ts_code": "600000.SH", "origin": "external_pool",
        }], snapshot)
        cls = output[0]["theme_classification"]
        self.assertIsNone(cls["primary_theme"])
        self.assertEqual(cls["classification_status"], "not_in_mapping")
        self.assertEqual(cls["business_verification_status"], "not_assessed_outside_229_mapping")

    def test_stats_are_scoped_and_show_external_coverage_gap(self):
        snapshot = theme.load_theme_classification(
            self.mapping_path, self.metadata_path, self.pool_codes,
        )
        official = theme.enrich_candidate_records([{
            "ts_code": "100000.SZ",
        }], snapshot)
        external = theme.enrich_candidate_records([{
            "ts_code": "600000.SH",
        }], snapshot)
        diagnostics = theme.build_theme_classification_diagnostics(
            snapshot,
            self.pool_codes,
            self.pool_codes,
            self.pool_codes,
            {"600000.SH", "600001.SH"},
            official,
            external,
        )
        self.assertFalse(diagnostics["market_wide_breadth"])
        self.assertTrue(diagnostics["production_pool_code_set_match"])
        self.assertEqual(diagnostics["scan_coverage"]["official"]["mapped_rows"], 229)
        self.assertEqual(diagnostics["scan_coverage"]["external"]["unmapped_rows"], 2)
        self.assertEqual(diagnostics["candidate_coverage"]["external"]["pending_classification_count"], 1)
        self.assertEqual(diagnostics["candidate_coverage"]["official"]["primary_theme_counts"], {"题材00": 1})


if __name__ == "__main__":
    unittest.main()
