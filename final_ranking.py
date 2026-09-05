"""Failure-tolerant final ranking for the V3 discovery pipeline.

This module only combines already-enriched data.  It performs no network I/O
and is intentionally disconnected from the V2 monitor and backtest paths.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Iterable, Mapping

from financial_crosscheck import CrosscheckResult
from financials import FinancialCandidate


FINAL_SCORE_WEIGHTS: Mapping[str, float] = {
    "preselection": 20.0,
    "financial": 40.0,
    "crosscheck": 15.0,
    "data_quality": 10.0,
    "small_investment": 10.0,
    "risk_adjustment": 5.0,
}

RISK_PENALTIES: Mapping[str, float] = {
    "negative_equity": 100.0,
    "edinet_negative_equity": 100.0,
    "consecutive_losses": 65.0,
    "edinet_shares_outstanding_increase": 65.0,
    "dilution_risk": 70.0,
    "major_financial_mismatch": 65.0,
    "large_negative_fcf": 35.0,
    "edinet_fcf_deterioration": 35.0,
    "declining_equity_ratio": 35.0,
    "edinet_declining_equity_ratio": 35.0,
    "rapid_debt_increase": 35.0,
    "extreme_valuation": 30.0,
    "insufficient_data_quality": 15.0,
}


@dataclass(frozen=True)
class FinalRankingConfig:
    weights: Mapping[str, float] = field(default_factory=lambda: dict(FINAL_SCORE_WEIGHTS))
    risk_penalties: Mapping[str, float] = field(default_factory=lambda: dict(RISK_PENALTIES))
    category_a: float = 80.0
    category_b: float = 70.0
    category_c: float = 60.0
    top_n: int = 20
    top_10_n: int = 10
    small_investment_n: int = 10
    small_investment_limit: float = 50_000.0
    growth_relief_threshold: float = 70.0
    growth_risk_relief: float = 0.50


@dataclass(frozen=True)
class RankingCandidate:
    code: str
    company_name: str
    market: str
    industry: str | None
    minimum_purchase_amount: float | None
    preselection_score: float | None
    financial_score: float | None
    crosscheck_score: float | None
    growth_score: float | None
    profitability_score: float | None
    valuation_score: float | None
    financial_health_score: float | None
    shareholder_return_score: float | None
    data_quality_score: float | None
    risk_flags: tuple[str, ...] = ()
    score_reasons: tuple[str, ...] = ()
    edinet_status: str = "unavailable"
    yahoo_status: str = "ok"
    source_date: str | None = None


@dataclass(frozen=True)
class FinalCandidate:
    rank: int
    code: str
    company_name: str
    market: str
    industry: str | None
    minimum_purchase_amount: float | None
    preselection_score: float | None
    financial_score: float | None
    crosscheck_score: float | None
    final_score: float
    category: str
    growth_score: float | None
    profitability_score: float | None
    valuation_score: float | None
    financial_health_score: float | None
    shareholder_return_score: float | None
    data_quality_score: float | None
    risk_flags: tuple[str, ...]
    score_reasons: tuple[str, ...]
    warning_reasons: tuple[str, ...]
    edinet_status: str
    yahoo_status: str
    source_date: str | None
    generated_at: str


@dataclass(frozen=True)
class FinalRankingResult:
    ranked_candidates: tuple[FinalCandidate, ...]
    top_20: tuple[FinalCandidate, ...]
    top_10: tuple[FinalCandidate, ...]
    small_investment_top_10: tuple[FinalCandidate, ...]


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def small_investment_score(amount: float | None) -> float | None:
    if amount is None or amount < 0:
        return None
    if amount <= 10_000:
        return 100.0
    if amount <= 30_000:
        return 80.0
    if amount <= 50_000:
        return 60.0
    if amount <= 100_000:
        return 50.0
    return 0.0


def classify_category(score: float, config: FinalRankingConfig = FinalRankingConfig()) -> str:
    if score >= config.category_a:
        return "A"
    if score >= config.category_b:
        return "B"
    if score >= config.category_c:
        return "C"
    return "D"


def _risk_component(candidate: RankingCandidate, config: FinalRankingConfig) -> tuple[float, tuple[str, ...]]:
    warnings: list[str] = []
    total_penalty = 0.0
    growth_relief = candidate.market == "Growth" and (candidate.growth_score or 0) >= config.growth_relief_threshold
    never_relieve = {"negative_equity", "edinet_negative_equity", "dilution_risk", "edinet_shares_outstanding_increase"}
    growth_phase = {"consecutive_losses", "large_negative_fcf", "extreme_valuation"}
    for flag in dict.fromkeys(candidate.risk_flags):
        penalty = float(config.risk_penalties.get(flag, 12.0 if flag.endswith("_mismatch") else 8.0))
        if growth_relief and flag in growth_phase and flag not in never_relieve:
            penalty *= config.growth_risk_relief
        total_penalty += penalty
        warnings.append(_warning_text(flag))
    if candidate.edinet_status == "no_recent_filing":
        warnings.append("EDINETの直近対象書類なし")
    elif candidate.edinet_status not in ("ok", "matched"):
        warnings.append("EDINET照合未取得")
    return _bounded(100.0 - total_penalty), tuple(dict.fromkeys(warnings))


def _warning_text(flag: str) -> str:
    labels = {
        "negative_equity": "債務超過",
        "edinet_negative_equity": "EDINETで債務超過",
        "consecutive_losses": "継続赤字",
        "large_negative_fcf": "FCF大幅マイナス",
        "edinet_fcf_deterioration": "EDINETでFCF悪化",
        "declining_equity_ratio": "自己資本比率低下",
        "edinet_declining_equity_ratio": "EDINETで自己資本比率低下",
        "rapid_debt_increase": "負債が急増",
        "extreme_valuation": "PER/PBRが極端",
        "dilution_risk": "希薄化リスク",
        "edinet_shares_outstanding_increase": "発行済株式数が増加",
        "insufficient_data_quality": "財務データ不足",
        "major_financial_mismatch": "財務データ重大不一致",
    }
    return labels.get(flag, "EDINET/Yahooの一部不一致" if flag.endswith("_mismatch") else flag)


def _positive_reasons(candidate: RankingCandidate, small_score: float | None) -> tuple[str, ...]:
    labels = {
        "strong_revenue_growth": "売上成長が高い",
        "strong_operating_margin": "営業利益率が良好",
        "operating_income_improving": "営業利益が改善",
        "reasonable_per": "PERが妥当",
        "strong_equity_ratio": "自己資本比率が良好",
        "positive_fcf": "FCFがプラス",
        "edinet_values_confirmed": "EDINET公式値で確認済み",
    }
    warning_codes = {"high_per", "weak_fcf", "limited_financial_data", "edinet_crosscheck_warning"}
    reasons = [labels.get(reason, reason) for reason in candidate.score_reasons if reason not in warning_codes]
    if (candidate.growth_score or 0) >= 70:
        reasons.append("売上・利益成長が高い")
    if (candidate.profitability_score or 0) >= 70:
        reasons.append("収益性が良好")
    if (candidate.valuation_score or 0) >= 70:
        reasons.append("割安度が良好")
    if (candidate.financial_health_score or 0) >= 70:
        reasons.append("財務健全性が高い")
    if (candidate.crosscheck_score or 0) >= 80:
        reasons.append("Yahoo/EDINET一致度が高い")
    if small_score is not None and small_score >= 60:
        reasons.append("最低購入金額が低い")
    return tuple(dict.fromkeys(reasons))


def score_final_candidate(candidate: RankingCandidate, config: FinalRankingConfig = FinalRankingConfig(),
                          *, generated_at: str | None = None) -> FinalCandidate:
    small = small_investment_score(candidate.minimum_purchase_amount)
    risk, warnings = _risk_component(candidate, config)
    reason_warnings = {
        "high_per": "PER高め", "weak_fcf": "FCFが弱い",
        "limited_financial_data": "財務データが限定的",
        "edinet_crosscheck_warning": "EDINETとの一部不一致",
    }
    warnings = tuple(dict.fromkeys((*warnings, *(reason_warnings[reason] for reason in candidate.score_reasons if reason in reason_warnings))))
    components = {
        "preselection": candidate.preselection_score,
        "financial": candidate.financial_score,
        "crosscheck": candidate.crosscheck_score,
        "data_quality": candidate.data_quality_score,
        "small_investment": small,
        "risk_adjustment": risk,
    }
    available = {key: value for key, value in components.items() if value is not None}
    denominator = sum(max(float(config.weights.get(key, 0)), 0.0) for key in available)
    weighted = sum(_bounded(value) * max(float(config.weights.get(key, 0)), 0.0) for key, value in available.items())
    score = _bounded(weighted / denominator if denominator else 0.0)
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    return FinalCandidate(
        0, candidate.code, candidate.company_name, candidate.market, candidate.industry,
        candidate.minimum_purchase_amount, candidate.preselection_score, candidate.financial_score,
        candidate.crosscheck_score, round(score, 2), classify_category(score, config),
        candidate.growth_score, candidate.profitability_score, candidate.valuation_score,
        candidate.financial_health_score, candidate.shareholder_return_score, candidate.data_quality_score,
        candidate.risk_flags, _positive_reasons(candidate, small), warnings,
        candidate.edinet_status, candidate.yahoo_status, candidate.source_date, timestamp,
    )


def from_financial_candidate(candidate: FinancialCandidate, *, industry: str | None = None,
                             crosscheck: CrosscheckResult | None = None,
                             edinet_status: str = "unavailable", yahoo_status: str = "ok") -> RankingCandidate:
    risks = list(candidate.risk_flags)
    crosscheck_score = None
    if crosscheck is not None:
        crosscheck_score = crosscheck.crosscheck_score
        risks.extend(crosscheck.edinet_risk_flags)
        risks.extend(crosscheck.warnings)
    return RankingCandidate(
        candidate.code, candidate.company_name, candidate.market, industry,
        candidate.minimum_purchase_amount, candidate.preselection_score, candidate.financial_score,
        crosscheck_score, candidate.growth_score, candidate.profitability_score,
        candidate.valuation_score, candidate.financial_health_score,
        candidate.shareholder_return_score, candidate.financial_data_quality_score,
        tuple(dict.fromkeys(risks)), candidate.score_reasons, edinet_status, yahoo_status,
        candidate.financial_data.period_end or candidate.fetched_at,
    )


def rank_final_candidates(candidates: Iterable[RankingCandidate],
                          config: FinalRankingConfig = FinalRankingConfig(),
                          *, generated_at: str | None = None) -> FinalRankingResult:
    timestamp = generated_at or datetime.now(timezone.utc).isoformat()
    scored = [score_final_candidate(candidate, config, generated_at=timestamp) for candidate in candidates]
    ordered = sorted(scored, key=lambda item: (-item.final_score, -float(item.financial_score or 0), item.code))
    ranked = tuple(replace(item, rank=index) for index, item in enumerate(ordered, 1))
    small = tuple(item for item in ranked if item.minimum_purchase_amount is not None
                  and item.minimum_purchase_amount <= config.small_investment_limit)
    return FinalRankingResult(
        ranked,
        ranked[:max(config.top_n, 0)],
        ranked[:max(config.top_10_n, 0)],
        small[:max(config.small_investment_n, 0)],
    )


def rank_financial_candidates(
    candidates: Iterable[FinancialCandidate], *,
    crosschecks: Mapping[str, CrosscheckResult] | None = None,
    edinet_statuses: Mapping[str, str] | None = None,
    yahoo_statuses: Mapping[str, str] | None = None,
    industries: Mapping[str, str | None] | None = None,
    config: FinalRankingConfig = FinalRankingConfig(),
    generated_at: str | None = None,
) -> FinalRankingResult:
    """Connect financials.py and financial_crosscheck.py to final ranking."""
    crosschecks = crosschecks or {}
    edinet_statuses = edinet_statuses or {}
    yahoo_statuses = yahoo_statuses or {}
    industries = industries or {}
    prepared = [
        from_financial_candidate(
            candidate,
            industry=industries.get(candidate.code),
            crosscheck=crosschecks.get(candidate.code),
            edinet_status=edinet_statuses.get(candidate.code, "unavailable"),
            yahoo_status=yahoo_statuses.get(candidate.code, "ok"),
        )
        for candidate in candidates
    ]
    return rank_final_candidates(prepared, config, generated_at=generated_at)
