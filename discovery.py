"""Extensible foundation for the V3 Japanese-stock discovery engine.

This module deliberately contains no universe downloader or disclosure/news
client.  Callers supply whatever data is available; missing metrics are
excluded from the weighted average instead of being treated as zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

from strategy import evaluate_frame


DEFAULT_WEIGHTS: Mapping[str, float] = {
    "affordability": 1.0,
    "revenue_growth": 1.2,
    "operating_profit_growth": 1.3,
    "valuation": 1.0,
    "market_cap": 0.6,
    "liquidity": 1.0,
    "technical": 1.2,
    "earnings_revision": 1.0,
    "dilution_risk": 1.2,
}


@dataclass(frozen=True)
class CandidateData:
    """Normalized input for one candidate; ratios use decimal notation."""

    code: str
    name: str = ""
    price: float | None = None
    lot_size: int | None = 100
    revenue_growth: float | None = None
    operating_profit_growth: float | None = None
    per: float | None = None
    pbr: float | None = None
    market_cap: float | None = None
    average_volume: float | None = None
    volume_ratio: float | None = None
    upward_revision: bool | None = None
    earnings_improvement: bool | None = None
    dilution_risk: bool | None = None
    warrant_risk: bool | None = None
    technical_frame: pd.DataFrame | None = field(default=None, repr=False, compare=False)
    technical_score: float | None = None

    @property
    def minimum_purchase_amount(self) -> float | None:
        if not _valid_number(self.price) or not _valid_number(self.lot_size):
            return None
        if float(self.price) <= 0 or int(self.lot_size) <= 0:
            return None
        return float(self.price) * int(self.lot_size)


@dataclass(frozen=True)
class CandidateScore:
    candidate: CandidateData
    total_score: float
    scores: Mapping[str, float]
    available_weight: float
    missing_items: tuple[str, ...]


@dataclass(frozen=True)
class ScreeningConfig:
    minimum_score: float = 40.0
    minimum_available_items: int = 2
    maximum_purchase_amount: float | None = None
    top_n: int = 10


@dataclass(frozen=True)
class DiscoveryResult:
    raw_candidates: tuple[CandidateData, ...]
    screened_candidates: tuple[CandidateScore, ...]
    top_candidates: tuple[CandidateScore, ...]


def _valid_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _linear(value: float, low: float, high: float) -> float:
    if high == low:
        return 100.0 if value >= high else 0.0
    return _clamp((value - low) / (high - low) * 100.0)


def score_affordability(candidate: CandidateData) -> float | None:
    """Favor one-lot prices around JPY 10k-30k, while remaining gradual."""

    amount = candidate.minimum_purchase_amount
    if amount is None:
        return None
    if 10_000 <= amount <= 30_000:
        return 100.0
    if amount < 10_000:
        return _linear(amount, 1_000, 10_000) * 0.3 + 65.0
    if amount <= 100_000:
        return _clamp(100.0 - (amount - 30_000) / 70_000 * 75.0)
    return _clamp(25.0 - (amount - 100_000) / 400_000 * 25.0)


def score_growth(value: float | None) -> float | None:
    if not _valid_number(value):
        return None
    # -20% => 0, 0% => 40, +30% => 100.
    value = float(value)
    return _linear(value, -0.20, 0.0) * 0.4 if value < 0 else 40.0 + _linear(value, 0.0, 0.30) * 0.6


def score_valuation(per: float | None, pbr: float | None) -> float | None:
    parts: list[float] = []
    if _valid_number(per) and float(per) > 0:
        value = float(per)
        parts.append(100.0 if value <= 10 else _clamp(100.0 - (value - 10) * 4.0))
    if _valid_number(pbr) and float(pbr) > 0:
        value = float(pbr)
        parts.append(100.0 if value <= 1 else _clamp(100.0 - (value - 1) * 35.0))
    return sum(parts) / len(parts) if parts else None


def score_market_cap(value: float | None) -> float | None:
    if not _valid_number(value) or float(value) <= 0:
        return None
    # JPY: favor investable small/mid caps without making size a hard filter.
    value = float(value)
    if 10_000_000_000 <= value <= 300_000_000_000:
        return 100.0
    if value < 10_000_000_000:
        return 40.0 + _linear(value, 0.0, 10_000_000_000) * 0.6
    return _clamp(100.0 - math.log10(value / 300_000_000_000) * 35.0)


def score_liquidity(average_volume: float | None, volume_ratio: float | None) -> float | None:
    parts: list[float] = []
    if _valid_number(average_volume) and float(average_volume) >= 0:
        parts.append(_linear(math.log10(max(float(average_volume), 1.0)), 3.0, 6.0))
    if _valid_number(volume_ratio) and float(volume_ratio) >= 0:
        parts.append(_linear(float(volume_ratio), 0.5, 2.0))
    return sum(parts) / len(parts) if parts else None


def score_earnings_revision(upward_revision: bool | None, improvement: bool | None) -> float | None:
    parts = [100.0 if value else 20.0 for value in (upward_revision, improvement) if value is not None]
    return sum(parts) / len(parts) if parts else None


def score_dilution_risk(dilution_risk: bool | None, warrant_risk: bool | None) -> float | None:
    risks = [value for value in (dilution_risk, warrant_risk) if value is not None]
    if not risks:
        return None
    return 100.0 * sum(not value for value in risks) / len(risks)


def score_technical(candidate: CandidateData) -> float | None:
    if _valid_number(candidate.technical_score):
        return _clamp(float(candidate.technical_score))
    if candidate.technical_frame is None or candidate.technical_frame.empty:
        return None
    try:
        _, decisions = evaluate_frame(candidate.technical_frame)
    except (KeyError, TypeError, ValueError):
        return None
    return next((float(item.score) for item in reversed(decisions) if item is not None), None)


def score_candidate(
    candidate: CandidateData,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> CandidateScore:
    scores = {
        "affordability": score_affordability(candidate),
        "revenue_growth": score_growth(candidate.revenue_growth),
        "operating_profit_growth": score_growth(candidate.operating_profit_growth),
        "valuation": score_valuation(candidate.per, candidate.pbr),
        "market_cap": score_market_cap(candidate.market_cap),
        "liquidity": score_liquidity(candidate.average_volume, candidate.volume_ratio),
        "technical": score_technical(candidate),
        "earnings_revision": score_earnings_revision(candidate.upward_revision, candidate.earnings_improvement),
        "dilution_risk": score_dilution_risk(candidate.dilution_risk, candidate.warrant_risk),
    }
    available = {key: value for key, value in scores.items() if value is not None and weights.get(key, 0) > 0}
    available_weight = sum(float(weights[key]) for key in available)
    total = (
        sum(float(value) * float(weights[key]) for key, value in available.items()) / available_weight
        if available_weight else 0.0
    )
    return CandidateScore(
        candidate=candidate,
        total_score=round(_clamp(total), 2),
        scores={key: round(float(value), 2) for key, value in available.items()},
        available_weight=round(available_weight, 2),
        missing_items=tuple(key for key, value in scores.items() if value is None),
    )


def screen_candidates(
    raw_candidates: Iterable[CandidateData],
    config: ScreeningConfig = ScreeningConfig(),
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> list[CandidateScore]:
    scored = [score_candidate(candidate, weights) for candidate in raw_candidates]
    screened = [
        item for item in scored
        if item.total_score >= config.minimum_score
        and len(item.scores) >= config.minimum_available_items
        and (
            config.maximum_purchase_amount is None
            or item.candidate.minimum_purchase_amount is None
            or item.candidate.minimum_purchase_amount <= config.maximum_purchase_amount
        )
    ]
    return sorted(screened, key=lambda item: (-item.total_score, item.candidate.code))


def top_candidates(
    candidates: Iterable[CandidateScore],
    limit: int = 10,
) -> list[CandidateScore]:
    if limit < 0:
        raise ValueError("limit must be zero or greater")
    return sorted(candidates, key=lambda item: (-item.total_score, item.candidate.code))[:limit]


def discover_candidates(
    raw_candidates: Iterable[CandidateData],
    config: ScreeningConfig = ScreeningConfig(),
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> DiscoveryResult:
    raw = tuple(raw_candidates)
    screened = tuple(screen_candidates(raw, config, weights))
    return DiscoveryResult(raw, screened, tuple(top_candidates(screened, config.top_n)))


def get_top_10_candidates(
    raw_candidates: Iterable[CandidateData],
    *,
    minimum_score: float = 40.0,
) -> list[CandidateScore]:
    """Convenience API for the requested V3 top-ten result."""

    config = ScreeningConfig(minimum_score=minimum_score, top_n=10)
    return list(discover_candidates(raw_candidates, config).top_candidates)
