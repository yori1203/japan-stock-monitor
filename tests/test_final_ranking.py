from dataclasses import replace

from final_ranking import (
    FinalRankingConfig, RankingCandidate, classify_category,
    rank_final_candidates, rank_financial_candidates, score_final_candidate, small_investment_score,
)
from financials import FinancialCandidate, FinancialData


def candidate(code="1000", **changes):
    base = RankingCandidate(
        code, f"会社{code}", "Prime", "情報・通信業", 40_000,
        75, 80, 90, 80, 75, 70, 80, 60, 85,
        (), (), "ok", "ok", "2026-03-31",
    )
    return replace(base, **changes)


def test_score_and_category():
    scored = score_final_candidate(candidate())
    assert 0 <= scored.final_score <= 100
    assert scored.category in "ABCD"


def test_weight_change():
    item = candidate(preselection_score=100, financial_score=0)
    financial = score_final_candidate(item, FinalRankingConfig(weights={"financial": 1})).final_score
    preselection = score_final_candidate(item, FinalRankingConfig(weights={"preselection": 1})).final_score
    assert financial == 0 and preselection == 100


def test_missing_edinet_is_renormalized_not_zero():
    item = candidate(crosscheck_score=None, edinet_status="unavailable")
    assert score_final_candidate(item).final_score > 60


def test_small_investment_bonus_is_bounded():
    assert small_investment_score(10_000) == 100
    assert small_investment_score(30_000) == 80
    assert small_investment_score(50_000) == 60
    assert small_investment_score(100_000) == 50
    assert small_investment_score(100_001) == 0
    cheap_weak = score_final_candidate(candidate(minimum_purchase_amount=9_000, financial_score=10)).final_score
    strong_expensive = score_final_candidate(candidate(minimum_purchase_amount=200_000, financial_score=95)).final_score
    assert strong_expensive > cheap_weak


def test_major_risk_reduces_score():
    safe = score_final_candidate(candidate()).final_score
    risky = score_final_candidate(candidate(risk_flags=("negative_equity",))).final_score
    assert risky < safe


def test_growth_market_relief_does_not_remove_hard_risk():
    growth = candidate(market="Growth", growth_score=90, risk_flags=("large_negative_fcf",))
    prime = replace(growth, market="Prime")
    assert score_final_candidate(growth).final_score > score_final_candidate(prime).final_score
    hard = replace(growth, risk_flags=("negative_equity",))
    assert score_final_candidate(hard).final_score < score_final_candidate(growth).final_score


def test_category_thresholds():
    config = FinalRankingConfig(category_a=80, category_b=70, category_c=60)
    assert [classify_category(v, config) for v in (80, 70, 60, 59.99)] == ["A", "B", "C", "D"]


def test_ranking_top_limits_and_small_top():
    items = [candidate(str(i), financial_score=float(i), minimum_purchase_amount=40_000 if i % 2 else 80_000) for i in range(30)]
    result = rank_final_candidates(items)
    assert len(result.top_20) == 20
    assert len(result.top_10) == 10
    assert len(result.small_investment_top_10) == 10
    assert result.ranked_candidates[0].final_score >= result.ranked_candidates[-1].final_score
    assert all(item.minimum_purchase_amount <= 50_000 for item in result.small_investment_top_10)


def test_reasons_warnings_and_no_recent_filing():
    scored = score_final_candidate(candidate(
        growth_score=90, crosscheck_score=95, risk_flags=("extreme_valuation",),
        edinet_status="no_recent_filing",
    ))
    assert "売上・利益成長が高い" in scored.score_reasons
    assert "PER/PBRが極端" in scored.warning_reasons
    assert "EDINETの直近対象書類なし" in scored.warning_reasons


def test_missing_amount_is_safe():
    assert score_final_candidate(candidate(minimum_purchase_amount=None)).final_score >= 0


def test_financial_layer_connection():
    financial = FinancialCandidate(
        "1000", "会社1000", "Prime", 30_000, 70, 80, 75, 70, 65, 85, 50, 90,
        (), ("strong_revenue_growth",), "2026-09-01T00:00:00+00:00",
        FinancialData("1000", period_end="2026-03-31"),
    )
    result = rank_financial_candidates([financial], industries={"1000": "情報・通信業"})
    assert result.top_10[0].industry == "情報・通信業"
    assert result.top_10[0].crosscheck_score is None
