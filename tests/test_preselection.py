import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from preselection import (
    MarketSnapshot,
    PreselectionConfig,
    classify_investment_amount,
    run_preselection,
    score_preselection,
)
from universe import UniverseSecurity


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def security(code, unit=100):
    return UniverseSecurity(code, f"Company {code}", "Prime", "情報・通信業", unit, "test", NOW)


def snapshot(code, *, price=200, value=5_000_000, zero_ratio=0, observations=252, change=0.1):
    return MarketSnapshot(code, price, value / price, value, change, change, price * 0.95,
                          price * 0.9, -0.1, zero_ratio, 0.02, observations, "2026-09-04", "test")


class Provider:
    def __init__(self, values=None, fail=False):
        self.values = values or {}
        self.fail = fail
        self.calls = 0

    def fetch_batch(self, securities):
        self.calls += 1
        if self.fail:
            raise OSError("offline")
        return {item.code: self.values[item.code] for item in securities if item.code in self.values}


class PreselectionTests(unittest.TestCase):
    def test_minimum_purchase_amount_and_category(self):
        candidate = score_preselection(security("1"), snapshot("1", price=200))
        self.assertEqual(candidate.minimum_purchase_amount, 20_000)
        self.assertEqual(candidate.investment_category, "10k_to_30k")

    def test_all_investment_categories(self):
        expected = [(10_000, "under_10k"), (10_001, "10k_to_30k"),
                    (30_001, "30k_to_50k"), (50_001, "50k_to_100k"),
                    (100_001, "over_100k"), (None, "unknown")]
        self.assertEqual([classify_investment_amount(value) for value, _ in expected],
                         [name for _, name in expected])

    def test_liquidity_filter_and_low_price_is_not_automatically_excluded(self):
        items = [security("1"), security("2")]
        provider = Provider({"1": snapshot("1", price=50, value=2_000_000),
                             "2": snapshot("2", price=50, value=100_000)})
        with tempfile.TemporaryDirectory() as folder:
            result = run_preselection(items, provider, cache_path=Path(folder) / "cache.json",
                                      config=PreselectionConfig(minimum_average_trading_value=1_000_000))
        self.assertEqual([item.security.code for item in result.eligible_candidates], ["1"])
        self.assertEqual(result.stats.excluded_count, 1)

    def test_missing_symbol_is_recorded_without_stopping_batch(self):
        items = [security("1"), security("2")]
        provider = Provider({"1": snapshot("1")})
        with tempfile.TemporaryDirectory() as folder:
            result = run_preselection(items, provider, cache_path=Path(folder) / "cache.json")
        self.assertEqual(result.stats.failed_count, 1)
        self.assertEqual(result.failed_symbols[0].code, "2")
        self.assertEqual(result.failed_symbols[0].failure_reason, "price_data_unavailable")

    def test_score_is_bounded_and_rewards_better_candidate(self):
        good = score_preselection(security("1"), snapshot("1", value=100_000_000, change=0.15))
        weak = score_preselection(security("2"), snapshot("2", price=2_000, value=1_000_000, change=-0.2))
        self.assertGreater(good.preselection_score, weak.preselection_score)
        self.assertGreaterEqual(weak.preselection_score, 0)
        self.assertLessEqual(good.preselection_score, 100)

    def test_ranking_and_top_limit(self):
        items = [security(str(index)) for index in range(5)]
        provider = Provider({item.code: snapshot(item.code, value=10 ** (index + 5), change=index / 20)
                             for index, item in enumerate(items)})
        with tempfile.TemporaryDirectory() as folder:
            result = run_preselection(items, provider, cache_path=Path(folder) / "cache.json",
                                      config=PreselectionConfig(minimum_average_trading_value=0, top_n=2))
        self.assertEqual(result.stats.returned_count, 2)
        self.assertGreaterEqual(result.ranked_candidates[0].preselection_score,
                                result.ranked_candidates[-1].preselection_score)

    def test_batch_failure_is_recorded_for_each_symbol(self):
        items = [security("1"), security("2")]
        with tempfile.TemporaryDirectory() as folder:
            result = run_preselection(items, Provider(fail=True), cache_path=Path(folder) / "cache.json")
        self.assertEqual(result.stats.failed_count, 2)
        self.assertTrue(all("offline" in item.failure_reason for item in result.failed_symbols))

    def test_same_day_cache_avoids_provider_call(self):
        items = [security("1")]
        day = date(2026, 9, 5)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.json"
            first = Provider({"1": snapshot("1")})
            run_preselection(items, first, cache_path=path, today=day)
            second = Provider(fail=True)
            result = run_preselection(items, second, cache_path=path, today=day)
        self.assertEqual(second.calls, 0)
        self.assertEqual(result.stats.cache_hit_count, 1)
        self.assertEqual(result.stats.failed_count, 0)

    def test_insufficient_data_is_excluded(self):
        items = [security("1")]
        provider = Provider({"1": snapshot("1", observations=20)})
        with tempfile.TemporaryDirectory() as folder:
            result = run_preselection(items, provider, cache_path=Path(folder) / "cache.json")
        self.assertEqual(result.stats.eligible_count, 0)
        self.assertEqual(result.stats.excluded_count, 1)


if __name__ == "__main__":
    unittest.main()
