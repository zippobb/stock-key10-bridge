import copy
import unittest
from datetime import date, timedelta
from candidate_sources_v2 import build_candidates


class RecallTests(unittest.TestCase):
    def setUp(self):
        self.dates = [(date(2026, 1, 1)+timedelta(days=i)).isoformat() for i in range(30)]
        self.benchmark = [dict(date=d, close=100) for d in self.dates]
        self.rows = []
        for i in range(45):
            bars = [dict(date=d, close=100, high=101, volume=100) for d in self.dates]
            event = 25 if i % 2 == 0 else 29
            for j in range(event, 30):
                bars[j].update(close=110+j-event, high=111+j-event, volume=200)
            self.rows.append(dict(ts_code=f'{i:06d}.SH', origin='official_pool' if i < 33 else 'external_pool',
                v2_universe_eligible=True, quant_rank=i+1, technical=20, final_score=65,
                timing_guard_triggered=False, bars=bars, themes=[dict(theme_id='test_theme',
                    effective_from=self.dates[0], published_on=self.dates[0], verified=True,
                    source_url='https://example.org/synthetic-fixture')]))

    def run_recall(self, rows=None):
        return build_candidates(self.rows if rows is None else rows, self.benchmark,
                                self.dates[-1], universe_complete=True)

    def test_quotas_pool_dedup_and_immutability(self):
        before = copy.deepcopy(self.rows)
        result = self.run_recall()
        self.assertEqual([len(v) for v in result['pools'].values()], [30, 10])
        selected = [r for p in result['pools'].values() for r in p]
        self.assertEqual(len({r['ts_code'] for r in selected}), 40)
        self.assertTrue(all(r['technical'] == 20 and r['final_score'] == 65 for r in selected))
        self.assertEqual(before, self.rows)

    def test_followers_require_temporal_order_and_guard(self):
        result = self.run_recall()
        self.assertGreater(result['route_counts']['confirmed_follower'], 0)
        for row in self.rows:
            row['timing_guard_triggered'] = True
        result = self.run_recall()
        self.assertEqual(result['route_counts']['confirmed_follower'], 0)
        self.assertGreater(result['route_counts']['early_leader'], 0)

    def test_future_prices_rejected(self):
        self.rows[0]['bars'][-1]['date'] = '2099-01-01'
        with self.assertRaisesRegex(ValueError, 'Future'):
            self.run_recall()

    def test_future_membership_not_used(self):
        for row in self.rows:
            row['themes'][0]['published_on'] = '2099-01-01'
        result = self.run_recall()
        self.assertTrue(result['coverage_gap'])
        self.assertEqual(result['route_counts']['theme_linkage'], 0)
        self.assertGreater(result['route_counts']['early_leader'], 0)

    def test_truncated_bundle_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Full eligible universe'):
            build_candidates(self.rows, self.benchmark, self.dates[-1])

    def test_one_stock_not_breadth(self):
        result = self.run_recall(self.rows[:1])
        self.assertTrue(result['coverage_gap'])
        self.assertEqual(result['route_counts']['theme_linkage'], 0)

    def test_deterministic(self):
        self.assertEqual(self.run_recall(), self.run_recall())

    def test_invalid_history_excluded(self):
        self.rows[0]['bars'][-1]['volume'] = float('nan')
        result = self.run_recall()
        self.assertIn(self.rows[0]['ts_code'], result['rejected'])


if __name__ == '__main__':
    unittest.main()
