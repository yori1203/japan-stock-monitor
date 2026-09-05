import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from financials import (
    FinancialConfig, FinancialData, YahooFinanceAdapter, data_quality_score,
    financial_health_score, growth_score, profitability_score, risk_flags,
    run_financial_enrichment, score_financial_candidate, shareholder_return_score,
    valuation_score,
)
from preselection import MarketSnapshot, score_preselection
from universe import UniverseSecurity


NOW = datetime(2026, 9, 5, tzinfo=timezone.utc)


def candidate(code="1", market="Prime", pre_score=70):
    security = UniverseSecurity(code, f"Company {code}", market, "情報・通信業", 100, "test", NOW)
    snapshot = MarketSnapshot(code, 200, 100_000, 20_000_000, .1, .2, 190, 180, -.1, 0, .02, 252, "2026-09-04", "test")
    result = score_preselection(security, snapshot)
    return type(result)(result.security, result.snapshot, result.minimum_purchase_amount,
                        result.investment_category, pre_score, result.component_scores)


def good_data(code="1", **changes):
    values = dict(code=code, revenue=1000, revenue_growth_yoy=.20, revenue_growth_3y_cagr=.15,
                  operating_income=180, operating_income_growth_yoy=.25, operating_margin=.18,
                  net_income=120, eps=30, eps_growth_yoy=.20, book_value_per_share=200,
                  per=15, pbr=1.5, roe=.18, roa=.09, equity_ratio=.60,
                  cash_and_equivalents=500, total_debt=200, free_cash_flow=100,
                  market_cap=10000, dividend_yield=.03, payout_ratio=.35,
                  fetched_at=NOW.isoformat(), source="test")
    values.update(changes)
    return FinancialData(**values)


class Adapter:
    def __init__(self, values=None, failures=0):
        self.values = values or {}
        self.failures = failures
        self.calls = 0

    def fetch(self, code):
        self.calls += 1
        if self.calls <= self.failures:
            raise OSError("temporary")
        if code not in self.values:
            raise LookupError("missing")
        return self.values[code]


class FinancialTests(unittest.TestCase):
    def test_growth_score(self):
        self.assertGreater(growth_score(good_data()), growth_score(good_data(revenue_growth_yoy=-.1, operating_income_growth_yoy=-.2, eps_growth_yoy=-.2, revenue_growth_3y_cagr=-.05)))

    def test_profitability_score(self):
        self.assertGreater(profitability_score(good_data()), profitability_score(good_data(operating_margin=-.05, roe=-.05, roa=-.03)))

    def test_valuation_score(self):
        self.assertGreater(valuation_score(good_data(per=10, pbr=1)), valuation_score(good_data(per=120, pbr=12)))

    def test_financial_health(self):
        healthy = good_data()
        weak = good_data(equity_ratio=.1, cash_and_equivalents=10, total_debt=1000, free_cash_flow=-100)
        self.assertGreater(financial_health_score(healthy), financial_health_score(weak))

    def test_shareholder_return_does_not_overpunish_no_dividend(self):
        self.assertEqual(shareholder_return_score(good_data(dividend_yield=None, payout_ratio=None)), 50)
        self.assertGreater(shareholder_return_score(good_data()), 50)

    def test_missing_values_remain_scorable_with_lower_quality(self):
        sparse = FinancialData("1", revenue=100, fetched_at=NOW.isoformat())
        scored = score_financial_candidate(candidate(), sparse, now=NOW)
        self.assertGreaterEqual(scored.financial_score, 0)
        self.assertLess(scored.financial_data_quality_score, data_quality_score(good_data(), NOW))

    def test_loss_company_is_not_excluded(self):
        data = good_data(operating_income=-50, operating_margin=-.05, consecutive_loss_years=2)
        scored = score_financial_candidate(candidate(), data, now=NOW)
        self.assertIn("operating_loss", scored.risk_flags)
        self.assertIn("consecutive_losses", scored.risk_flags)

    def test_high_growth_high_per_growth_company_gets_balanced_score(self):
        data = good_data(revenue_growth_yoy=.35, earnings_growth_forecast=.30, per=60)
        self.assertGreaterEqual(valuation_score(data, "Growth"), 50)

    def test_risk_flags(self):
        data = good_data(free_cash_flow=-200, equity_ratio=-.1, previous_equity_ratio=.2,
                         total_debt=200, previous_total_debt=100, per=150, dilution_risk=.8)
        flags = risk_flags(data, 40)
        self.assertTrue({"large_negative_fcf", "negative_equity", "declining_equity_ratio",
                         "rapid_debt_increase", "extreme_valuation", "insufficient_data_quality",
                         "dilution_risk"}.issubset(flags))

    def test_data_quality_fresh_complete_beats_stale_sparse(self):
        stale = FinancialData("1", revenue=1, fetched_at=(NOW - timedelta(days=800)).isoformat())
        self.assertGreater(data_quality_score(good_data(), NOW), data_quality_score(stale, NOW))

    def test_data_quality_uses_financial_period_not_retrieval_time(self):
        old_period = good_data(period_end=(NOW - timedelta(days=800)).date().isoformat())
        current_period = good_data(period_end=NOW.date().isoformat())
        self.assertGreater(data_quality_score(current_period, NOW), data_quality_score(old_period, NOW))

    def test_cache_avoids_fetch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "financials.json"
            first = Adapter({"1": good_data()})
            run_financial_enrichment([candidate()], first, cache_path=path, now=NOW,
                                     config=FinancialConfig(batch_delay=0, retry_delay=0))
            second = Adapter(failures=10)
            result = run_financial_enrichment([candidate()], second, cache_path=path, now=NOW,
                                              config=FinancialConfig(batch_delay=0, retry_delay=0))
        self.assertEqual(second.calls, 0)
        self.assertEqual(result.stats.cache_hit_count, 1)

    def test_retry(self):
        adapter = Adapter({"1": good_data()}, failures=1)
        with tempfile.TemporaryDirectory() as folder:
            result = run_financial_enrichment([candidate()], adapter, cache_path=Path(folder) / "x.json",
                                              now=NOW, sleeper=lambda _: None,
                                              config=FinancialConfig(max_retries=2, batch_delay=0, retry_delay=0))
        self.assertEqual(adapter.calls, 2)
        self.assertEqual(result.stats.success_count, 1)

    def test_ranking(self):
        values = {"1": good_data("1"), "2": good_data("2", revenue_growth_yoy=-.1, per=120)}
        with tempfile.TemporaryDirectory() as folder:
            result = run_financial_enrichment([candidate("2"), candidate("1")], Adapter(values),
                                              cache_path=Path(folder) / "x.json", now=NOW,
                                              config=FinancialConfig(batch_delay=0, retry_delay=0))
        self.assertEqual(result.financially_ranked[0].code, "1")

    def test_top_limit_and_failure_isolation(self):
        items = [candidate(str(i)) for i in range(4)]
        values = {str(i): good_data(str(i)) for i in range(3)}
        with tempfile.TemporaryDirectory() as folder:
            result = run_financial_enrichment(items, Adapter(values), cache_path=Path(folder) / "x.json", now=NOW,
                                              config=FinancialConfig(top_n=2, max_retries=0, batch_delay=0, retry_delay=0))
        self.assertEqual(result.stats.returned_count, 2)
        self.assertEqual(result.stats.failed_count, 1)
        self.assertEqual(result.failed_symbols[0].code, "3")


if __name__ == "__main__":
    unittest.main()
