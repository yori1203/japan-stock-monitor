import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from universe import (
    PrimaryFilter,
    QuoteSnapshot,
    UniverseSecurity,
    acquire_universe,
    classify_purchase_amount,
    filter_equity_universe,
    primary_filter,
    save_cache,
    to_raw_candidates,
)


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def security(code, market="Prime", security_type="common_stock", unit=100):
    return UniverseSecurity(code, f"Company {code}", market, "情報・通信業", unit, "test", NOW, security_type)


class UniverseTests(unittest.TestCase):
    def test_market_filter(self):
        items = [security("1", "Prime"), security("2", "Standard"), security("3", "Growth")]
        result = filter_equity_universe(items, markets=frozenset({"Growth"}))
        self.assertEqual([item.code for item in result], ["3"])

    def test_etf_and_other_products_are_excluded(self):
        items = [security("1"), security("2", security_type="fund_or_other")]
        self.assertEqual([item.code for item in filter_equity_universe(items)], ["1"])

    def test_minimum_purchase_amount(self):
        self.assertEqual(security("1", unit=100).minimum_purchase_amount(250.5), 25_050)
        self.assertIsNone(security("2", unit=None).minimum_purchase_amount(250))

    def test_purchase_amount_categories(self):
        self.assertEqual(classify_purchase_amount(10_000), "under_10k")
        self.assertEqual(classify_purchase_amount(10_001), "10k_to_30k")
        self.assertEqual(classify_purchase_amount(30_001), "30k_to_50k")
        self.assertEqual(classify_purchase_amount(50_001), "over_50k")
        self.assertEqual(classify_purchase_amount(None), "unknown")

    def test_primary_filter_handles_missing_price_and_liquidity(self):
        items = [security("1"), security("2"), security("3")]
        quotes = {
            "1": QuoteSnapshot(200, average_volume=50_000),
            "2": QuoteSnapshot(None, average_volume=50_000),
            "3": QuoteSnapshot(200, average_volume=None),
        }
        config = PrimaryFilter(minimum_average_volume=10_000, maximum_purchase_amount=30_000)
        self.assertEqual([item.code for item in primary_filter(items, quotes, config)], ["1"])

    def test_stale_cache_is_used_when_fetch_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "universe.json"
            cached_at = NOW - timedelta(days=30)
            save_cache(path, [security("1234")], cached_at)

            def fail():
                raise OSError("offline")

            result = acquire_universe(path, max_age=timedelta(days=7), fetcher=fail, now=NOW)
        self.assertTrue(result.used_cache)
        self.assertFalse(result.used_fallback)
        self.assertEqual(result.source, "stale-cache")
        self.assertEqual(result.securities[0].code, "1234")

    def test_bundled_fallback_is_used_without_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.json"

            def fail():
                raise OSError("offline")

            result = acquire_universe(path, fetcher=fail, now=NOW)
        self.assertTrue(result.used_fallback)
        self.assertGreater(len(result.securities), 0)
        self.assertIn("offline", result.warning)

    def test_discovery_adapter_produces_raw_candidates(self):
        items = [security("1", unit=100)]
        result = to_raw_candidates(items, {"1": QuoteSnapshot(200, 100_000, 1.5)}, technical_scores={"1": 80})
        self.assertEqual(result[0].code, "1")
        self.assertEqual(result[0].minimum_purchase_amount, 20_000)
        self.assertEqual(result[0].technical_score, 80)


if __name__ == "__main__":
    unittest.main()
