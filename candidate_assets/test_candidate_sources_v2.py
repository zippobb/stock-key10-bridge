import copy
import unittest
from datetime import date, timedelta

from candidate_sources_v2 import build_candidates


BASIS = "raw_unadjusted_close_volume"


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.dates = [
            (date(2026, 1, 1) + timedelta(days=i)).isoformat()
            for i in range(30)
        ]
        self.benchmark = [
            dict(date=d, close=100, price_basis=BASIS)
            for d in self.dates
        ]
        self.rows = []
        for i in range(45):
            bars = [
                dict(
                    date=d,
                    close=100,
                    high=101,
                    low=99,
                    volume=100,
                    price_basis=BASIS,
                )
                for d in self.dates
            ]
            event = 25 if i % 2 == 0 else 29
            for j in range(event, 30):
                bars[j].update(
                    close=110 + j - event,
                    high=111 + j - event,
                    volume=200,
                )
            self.rows.append(
                dict(
                    ts_code=f"{i:06d}.SH",
                    origin=(
                        "official_pool" if i < 33 else "external_pool"
                    ),
                    v2_universe_eligible=True,
                    quant_rank=i + 1,
                    technical=20,
                    final_score=65,
                    timing_guard_triggered=False,
                    bars=bars,
                    themes=[
                        dict(
                            theme_id="test_theme",
                            effective_from=self.dates[0],
                            published_on=self.dates[0],
                            verified=True,
                            source_url="https://example.org/synthetic-fixture",
                        )
                    ],
                )
            )

    def run_recall(self, rows=None):
        return build_candidates(
            self.rows if rows is None else rows,
            self.benchmark,
            self.dates[-1],
            universe_complete=True,
        )

    def test_quotas_pool_dedup_and_immutability(self):
        before = copy.deepcopy(self.rows)
        result = self.run_recall()
        self.assertEqual(
            [len(v) for v in result["pools"].values()],
            [30, 10],
        )
        selected = [
            row for pool in result["pools"].values() for row in pool
        ]
        self.assertEqual(
            len({row["ts_code"] for row in selected}),
            40,
        )
        self.assertTrue(
            all(
                row["technical"] == 20 and row["final_score"] == 65
                for row in selected
            )
        )
        self.assertEqual(before, self.rows)

    def test_followers_require_temporal_order_and_guard(self):
        result = self.run_recall()
        self.assertGreater(
            result["route_counts"]["confirmed_follower"],
            0,
        )
        for row in self.rows:
            row["timing_guard_triggered"] = True
        result = self.run_recall()
        self.assertEqual(
            result["route_counts"]["confirmed_follower"],
            0,
        )
        self.assertGreater(result["route_counts"]["early_leader"], 0)

    def test_unrelated_invalid_history_does_not_block_other_theme(self):
        for row in self.rows:
            row["themes"][0]["theme_id"] = "good_theme"
        self.rows[0]["themes"][0]["theme_id"] = "unrelated_theme"
        self.rows[0]["bars"][-1]["volume"] = float("nan")
        result = self.run_recall()
        self.assertTrue(result["theme_stats"]["good_theme"]["linked"])
        self.assertEqual(
            result["theme_stats"]["good_theme"]["invalid_history_members"],
            [],
        )

    def test_missing_theme_member_keeps_denominator(self):
        for row in self.rows:
            row["themes"] = []
        for row in self.rows[:4]:
            row["themes"] = [
                dict(
                    theme_id="sparse_theme",
                    effective_from=self.dates[0],
                    published_on=self.dates[0],
                    verified=True,
                    source_url="https://example.org/synthetic-fixture",
                )
            ]
        self.rows[0]["bars"][-1]["volume"] = float("nan")
        result = self.run_recall()
        stats = result["theme_stats"]["sparse_theme"]
        self.assertEqual(len(stats["members"]), 4)
        self.assertEqual(len(stats["invalid_history_members"]), 1)
        self.assertAlmostEqual(stats["breadth"], 0.75)
        self.assertFalse(stats["linked"])

    def test_event_membership_is_historical(self):
        # Row 2 breaks out on session 25 but is tagged only from trade_date.
        self.rows[2]["themes"][0]["effective_from"] = self.dates[-1]
        self.rows[2]["themes"][0]["published_on"] = self.dates[-1]
        result = self.run_recall()
        self.assertIn("theme_linkage", result["pools"]["official_pool"][0]["candidate_source"]["routes"])
        self.assertNotIn(
            "confirmed_follower",
            next(
                row["candidate_source"]["routes"]
                for row in result["pools"]["official_pool"]
                if row["ts_code"] == "000002.SH"
            ),
        )

    def test_future_prices_rejected(self):
        self.rows[0]["bars"][-1]["date"] = "2099-01-01"
        with self.assertRaisesRegex(ValueError, "Future"):
            self.run_recall()

    def test_future_membership_not_used(self):
        for row in self.rows:
            row["themes"][0]["published_on"] = "2099-01-01"
        result = self.run_recall()
        self.assertTrue(result["coverage_gap"])
        self.assertEqual(result["route_counts"]["theme_linkage"], 0)
        self.assertGreater(result["route_counts"]["early_leader"], 0)

    def test_price_basis_mismatch_rejected(self):
        self.rows[0]["bars"][0]["price_basis"] = "adjusted_close"
        result = self.run_recall()
        self.assertEqual(
            result["rejected"]["000000.SH"],
            "price_basis_unverified_or_mismatch",
        )

    def test_truncated_bundle_rejected(self):
        with self.assertRaisesRegex(ValueError, "Full eligible universe"):
            build_candidates(
                self.rows,
                self.benchmark,
                self.dates[-1],
            )

    def test_one_stock_not_breadth(self):
        result = self.run_recall(self.rows[:1])
        self.assertTrue(result["coverage_gap"])
        self.assertEqual(result["route_counts"]["theme_linkage"], 0)

    def test_deterministic(self):
        self.assertEqual(self.run_recall(), self.run_recall())

    def test_invalid_history_excluded(self):
        self.rows[0]["bars"][-1]["volume"] = float("nan")
        result = self.run_recall()
        self.assertIn(self.rows[0]["ts_code"], result["rejected"])


if __name__ == "__main__":
    unittest.main()

