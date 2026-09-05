import unittest

import pandas as pd

from discovery import (
    CandidateData,
    ScreeningConfig,
    discover_candidates,
    get_top_10_candidates,
    score_candidate,
)


class DiscoveryScoreTests(unittest.TestCase):
    def test_low_cost_lot_receives_affordability_bonus(self):
        candidate = CandidateData(code="1111", price=200, lot_size=100, revenue_growth=0.1)
        result = score_candidate(candidate)
        self.assertEqual(candidate.minimum_purchase_amount, 20_000)
        self.assertEqual(result.scores["affordability"], 100.0)
        self.assertGreaterEqual(result.total_score, 0)
        self.assertLessEqual(result.total_score, 100)

    def test_available_data_is_scored_when_other_items_are_missing(self):
        result = score_candidate(CandidateData(code="2222", revenue_growth=0.25))
        self.assertGreater(result.total_score, 0)
        self.assertEqual(set(result.scores), {"revenue_growth"})
        self.assertIn("valuation", result.missing_items)
        self.assertIn("technical", result.missing_items)

    def test_existing_strategy_supplies_technical_score(self):
        index = pd.date_range("2025-01-01", periods=100, freq="B")
        close = [100 + position * 0.5 for position in range(100)]
        frame = pd.DataFrame({"Open": close, "Close": close, "Volume": [2_000] * 100}, index=index)
        result = score_candidate(CandidateData(code="3333", technical_frame=frame))
        self.assertIn("technical", result.scores)
        self.assertGreaterEqual(result.scores["technical"], 0)
        self.assertLessEqual(result.scores["technical"], 100)


class DiscoveryPipelineTests(unittest.TestCase):
    def test_raw_screened_and_top_stages_are_distinct(self):
        raw = [
            CandidateData(code="0003", price=200, revenue_growth=0.30, technical_score=90),
            CandidateData(code="0001", price=250, revenue_growth=0.20, technical_score=80),
            CandidateData(code="0002", price=5_000, revenue_growth=-0.20, technical_score=10),
        ]
        result = discover_candidates(raw, ScreeningConfig(minimum_score=50, top_n=1))
        self.assertEqual(len(result.raw_candidates), 3)
        self.assertEqual([item.candidate.code for item in result.screened_candidates], ["0003", "0001"])
        self.assertEqual([item.candidate.code for item in result.top_candidates], ["0003"])

    def test_top_10_is_limited_and_sorted(self):
        raw = [CandidateData(code=f"{index:04d}", technical_score=index + 50,
                             revenue_growth=0.1) for index in range(15)]
        top = get_top_10_candidates(raw)
        self.assertEqual(len(top), 10)
        self.assertEqual(top[0].candidate.code, "0014")
        self.assertGreaterEqual(top[0].total_score, top[-1].total_score)

    def test_purchase_cap_does_not_reject_unknown_amount(self):
        raw = [CandidateData(code="9999", revenue_growth=0.20, technical_score=75)]
        result = discover_candidates(
            raw,
            ScreeningConfig(minimum_score=0, minimum_available_items=2, maximum_purchase_amount=30_000),
        )
        self.assertEqual([item.candidate.code for item in result.screened_candidates], ["9999"])


if __name__ == "__main__":
    unittest.main()
