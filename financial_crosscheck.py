"""Yahoo/EDINET financial cross-checking without changing the Yahoo-only path."""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Mapping

from edinet_adapter import EdinetFinancialData
from financials import FinancialCandidate, FinancialData


@dataclass(frozen=True)
class CrosscheckConfig:
    warning_ratio: float = 0.10
    unit_multipliers: tuple[float, ...] = (1.0, 1_000.0, 1_000_000.0)
    period_tolerance_days: int = 100


@dataclass(frozen=True)
class FieldCrosscheck:
    field: str
    yahoo_value: float | None
    edinet_value: float | None
    difference: float | None
    difference_ratio: float | None
    status: str
    unit_multiplier: float = 1.0


@dataclass(frozen=True)
class CrosscheckResult:
    fields: tuple[FieldCrosscheck, ...]
    crosscheck_score: float
    warnings: tuple[str, ...]
    edinet_risk_flags: tuple[str, ...]


def _best_unit(yahoo: float, edinet: float, multipliers: tuple[float, ...]) -> tuple[float, float]:
    options = [(factor, abs(yahoo - edinet * factor) / max(abs(yahoo), abs(edinet * factor), 1)) for factor in multipliers]
    return min(options, key=lambda item: item[1])


def edinet_risk_flags(data: EdinetFinancialData) -> tuple[str, ...]:
    flags=[]
    if data.operating_income is not None and data.operating_income < 0: flags.append("edinet_operating_loss")
    if data.equity is not None and data.equity < 0: flags.append("edinet_negative_equity")
    if data.previous_equity is not None and data.previous_total_assets and data.equity is not None and data.total_assets:
        if data.previous_equity/data.previous_total_assets - data.equity/data.total_assets >= .10: flags.append("edinet_declining_equity_ratio")
    if data.free_cash_flow is not None and data.previous_free_cash_flow is not None and data.free_cash_flow < data.previous_free_cash_flow * .5: flags.append("edinet_fcf_deterioration")
    if data.shares_outstanding_growth is not None and data.shares_outstanding_growth >= .10: flags.append("edinet_shares_outstanding_increase")
    return tuple(flags)


def financial_crosscheck(yahoo: FinancialData, edinet: EdinetFinancialData,
                         config: CrosscheckConfig = CrosscheckConfig()) -> CrosscheckResult:
    pairs: Mapping[str, tuple[float | None, float | None]] = {
        "revenue": (yahoo.revenue, edinet.revenue), "operating_income": (yahoo.operating_income, edinet.operating_income),
        "net_income": (yahoo.net_income, edinet.net_income), "equity": (None, edinet.equity),
        "total_assets": (None, edinet.total_assets), "eps": (yahoo.eps, edinet.eps),
    }
    results=[]; matched=0; comparable=0; warnings=[]
    for name,(yv,ev) in pairs.items():
        if yv is None or ev is None:
            results.append(FieldCrosscheck(name,yv,ev,None,None,"unavailable")); continue
        factor,ratio=_best_unit(yv,ev,config.unit_multipliers); adjusted=ev*factor
        difference=yv-adjusted; status="matched" if ratio <= config.warning_ratio else "warning"
        comparable+=1; matched += status == "matched"
        if status == "warning": warnings.append(f"{name}_mismatch")
        results.append(FieldCrosscheck(name,yv,ev,difference,ratio,status,factor))
    score=50.0 if not comparable else matched/comparable*100.0
    return CrosscheckResult(tuple(results),round(score,2),tuple(warnings),edinet_risk_flags(edinet))


def apply_edinet_crosscheck(candidate: FinancialCandidate, edinet: EdinetFinancialData | None,
                            config: CrosscheckConfig = CrosscheckConfig()) -> FinancialCandidate:
    """Return the Yahoo score unchanged unless official EDINET data exists."""
    if edinet is None: return candidate
    check=financial_crosscheck(candidate.financial_data,edinet,config)
    quality=min(100.0,candidate.financial_data_quality_score + (10 if check.crosscheck_score >= 80 else 0))
    health=candidate.financial_health_score
    if edinet.equity is not None and edinet.total_assets:
        official_ratio=edinet.equity/edinet.total_assets
        health=(health + max(0,min(100,official_ratio/.7*100)))/2
    delta=(health-candidate.financial_health_score)*.20
    score=max(0,min(100,candidate.financial_score+delta))
    flags=tuple(dict.fromkeys(candidate.risk_flags+check.edinet_risk_flags+check.warnings))
    reasons=candidate.score_reasons + (("edinet_values_confirmed",) if check.crosscheck_score>=80 else ("edinet_crosscheck_warning",))
    return replace(candidate,financial_score=round(score,2),financial_health_score=round(health,2),
                   financial_data_quality_score=round(quality,2),risk_flags=flags,score_reasons=reasons)
