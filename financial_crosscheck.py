"""Yahoo/EDINET financial cross-checking without changing the Yahoo-only path."""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Mapping

from edinet_adapter import EdinetFinancialData
from financials import FinancialCandidate, FinancialData


@dataclass(frozen=True)
class CrosscheckConfig:
    warning_ratio: float = 0.10
    unit_multipliers: tuple[float, ...] = (1.0, 1_000.0, 1_000_000.0)
    period_tolerance_days: int = 100
    require_provenance: bool = False


@dataclass(frozen=True)
class FieldCrosscheck:
    field: str
    yahoo_value: float | None
    edinet_value: float | None
    difference: float | None
    difference_ratio: float | None
    status: str
    unit_multiplier: float = 1.0
    numeric_status: str = ""
    diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CrosscheckResult:
    fields: tuple[FieldCrosscheck, ...]
    crosscheck_score: float
    warnings: tuple[str, ...]
    edinet_risk_flags: tuple[str, ...]
    period_mismatch: bool = False


def _best_unit(yahoo: float, edinet: float, multipliers: tuple[float, ...]) -> tuple[float, float]:
    options = [(factor, abs(yahoo - edinet * factor) / max(abs(yahoo), abs(edinet * factor), 1)) for factor in multipliers]
    return min(options, key=lambda item: item[1])


def select_comparison_fact(name, ym, em, value):
    """Select statement totals by context/meaning, never by distance to Yahoo.

    EDINET's standard consolidation axis defaults to ConsolidatedMember.
    Segment/equity-component dimensions cannot compete with statement totals.
    Yahoo does not supply scope; this function does not invent Yahoo evidence.
    """
    candidates = em.get("all_period_candidates", em.get("candidates", []))
    if not candidates:
        return value, em
    totals = []
    for original in candidates:
        c = dict(original)
        dims = c.get("dimensions")
        if dims is None or any(d.rsplit(":", 1)[-1] not in
                              ("ConsolidatedMember", "NonConsolidatedMember") for d in dims):
            continue
        if (not dims and c.get("scope") == "unknown" and
                c.get("tag", "").startswith(("{http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/",
                                               "{http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/"))):
            c["scope"] = "consolidated"
            c["scope_basis"] = "EDINET standard consolidation-axis default member"
        totals.append(c)
    if not totals:
        return value, {**em, "selection_basis": "no_statement_total_context"}
    # Keep the same selected period unless a fact for Yahoo's date and duration
    # is actually present in this filing. No extrapolation or half-year doubling.
    def same_period(c):
        if not ym.get("period_end") or c.get("period_end") != ym["period_end"]:
            return False
        if ym.get("period_kind") == "instant":
            return not c.get("period_start")
        if ym.get("period_start"):
            return c.get("period_start") == ym["period_start"]
        if ym.get("period_kind") == "annual" and c.get("period_start"):
            try:
                return 330 <= (date.fromisoformat(c["period_end"]) - date.fromisoformat(c["period_start"])).days <= 400
            except ValueError:
                return False
        return False
    pool = [c for c in totals if same_period(c)]
    if not pool:
        pool = [c for c in totals if c.get("period_end") == em.get("period_end")]
    if not pool:
        return value, em
    desired_scope = ym.get("scope")
    scoped = [c for c in pool if c.get("scope") == desired_scope] if desired_scope in ("consolidated", "non_consolidated") else []
    if not scoped:
        scoped = [c for c in pool if c.get("scope") == "consolidated"]
    pool = scoped or pool
    source_field = ym.get("source_field")
    if name == "net_income" and source_field in ("Net Income", "Net Income Common Stockholders"):
        owners = [c for c in pool if c.get("tag_local") in
                  ("ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToOwnersOfParentIFRS")]
        pool = owners or pool
    if name == "equity" and source_field == "Stockholders Equity":
        owners = [c for c in pool if c.get("tag_local") in
                  ("EquityAttributableToOwnersOfParent", "EquityAttributableToOwnersOfParentIFRS")]
        pool = owners or pool
    # Different values in the remaining equivalent contexts remain ambiguous.
    chosen = sorted(pool, key=lambda c: (c.get("tag", ""), c.get("context_id", "")))[0]
    metadata = {**chosen, "candidates": candidates,
                **({"statement_tag_audit": em["statement_tag_audit"]} if "statement_tag_audit" in em else {}),
                "selection_ambiguous": len({(c.get("normalized_value"), c.get("original_unit"), c.get("period_start"), c.get("scope")) for c in pool}) > 1,
                "candidate_count": len(candidates), "eligible_candidate_count": len(pool),
                "selection_basis": "statement_total; observed_period; scope; source_field_semantics",
                "original_selected_value": em.get("original_selected_value", value),
                "original_selected_context": em.get("original_selected_context", em.get("context_id"))}
    if name == "equity" and source_field == "Stockholders Equity" and chosen.get("tag_local") in ("NetAssets", "Equity"):
        metadata["semantic_mismatch"] = "total_equity_including_other_interests_vs_stockholders_equity"
    return chosen["normalized_value"], metadata


def comparison_diagnostics(name, yahoo, edinet, factor, *, missing=False):
    """Separate proven incompatibility from missing comparison evidence."""
    ym = yahoo.field_metadata.get(name, {})
    em = edinet.field_metadata.get(name, {})
    causes, unknown = [], []
    if missing:
        causes.append("data_missing")
    ye, ee = ym.get("period_end"), em.get("period_end")
    if ye and ee:
        if ye != ee: causes.append("period_mismatch")
        elif name not in ("equity", "total_assets"):
            ys, es = ym.get("period_start"), em.get("period_start")
            if ys and es:
                if ys != es: causes.append("period_mismatch")
            elif ym.get("period_kind") == "annual" and es:
                try:
                    days = (date.fromisoformat(ee) - date.fromisoformat(es)).days
                    if not 330 <= days <= 400: causes.append("period_mismatch")
                    else: unknown.append("period_start_unknown")
                except ValueError: unknown.append("invalid_period")
            else: unknown.append("period_start_unknown")
    else: unknown.append("period_unknown")
    scopes = (ym.get("scope", "unknown"), em.get("scope", "unknown"))
    if "unknown" not in scopes and all(scopes):
        if scopes[0] != scopes[1]: causes.append("scope_mismatch")
    else: unknown.append("scope_unknown")
    yu, eu = ym.get("original_unit", "unknown"), em.get("original_unit", "unknown")
    if yu not in (None, "unknown") and eu not in (None, "unknown"):
        if yu != eu: causes.append("unit_mismatch")
    else: unknown.append("unit_unknown")
    if em.get("selection_ambiguous") or (
        ym.get("semantic") and em.get("semantic") and ym["semantic"] != em["semantic"]):
        causes.append("xbrl_tag_selection")
    if em.get("semantic_mismatch"):
        causes.append("semantic_mismatch")
    if ym.get("period_kind") == "trailing" and not ym.get("period_end"):
        causes.append("undated_trailing_period")
    if not em.get("tag"): unknown.append("xbrl_tag_unknown")
    if factor != 1:
        causes.append("unit_correction")
        unknown.append("heuristic_multiplier_unverified")
    return {"edinet": em, "yahoo": ym, "causes": list(dict.fromkeys(causes)),
            "unknown": unknown, "eligible": not causes and not unknown,
            "reason": "; ".join(causes + unknown) or "comparison_conditions_aligned"}


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
        "net_income": (yahoo.net_income, edinet.net_income), "equity": (yahoo.equity, edinet.equity),
        "total_assets": (yahoo.total_assets, edinet.total_assets), "eps": (yahoo.eps, edinet.eps),
    }
    results=[]; matched=0; comparable=0; warnings=[]
    for name,(yv,ev) in pairs.items():
        field_edinet = edinet
        if config.require_provenance and ev is not None:
            ev, selected_meta = select_comparison_fact(name, yahoo.field_metadata.get(name, {}),
                                                       edinet.field_metadata.get(name, {}), ev)
            field_edinet = replace(edinet, **{name: ev}, field_metadata={**edinet.field_metadata, name: selected_meta})
        if yv is None or ev is None:
            results.append(FieldCrosscheck(name,yv,ev,None,None,"unavailable",
                diagnostics=comparison_diagnostics(name,yahoo,edinet,1,missing=True))); continue
        ym, em = yahoo.field_metadata.get(name, {}), field_edinet.field_metadata.get(name, {})
        # Values already normalised from explicit XBRL units/scales must not be
        # scaled again simply to make a mismatch smaller.
        known_units = ym.get("original_unit") not in (None,"unknown") and em.get("original_unit") not in (None,"unknown")
        multipliers = (1.0,) if known_units else config.unit_multipliers
        factor,ratio=_best_unit(yv,ev,multipliers); adjusted=ev*factor
        difference=yv-adjusted; status="matched" if ratio <= config.warning_ratio else "warning"
        numeric_status = status
        diagnostics = comparison_diagnostics(name,yahoo,field_edinet,factor)
        if config.require_provenance:
            if any(c in diagnostics["causes"] for c in ("period_mismatch","scope_mismatch","unit_mismatch","xbrl_tag_selection","semantic_mismatch","undated_trailing_period")):
                status = "not_comparable"
            elif not diagnostics["eligible"]:
                # Numeric agreement cannot establish period, scope or units.
                # Keep numeric_status for audit, but never call it matched.
                status = "not_comparable"
                diagnostics["causes"].append("comparison_basis_unavailable")
                diagnostics["reason"] += "; comparison_basis_unavailable: " + ", ".join(diagnostics["unknown"])
            if diagnostics["eligible"] and status == "warning":
                diagnostics["causes"].append("substantive_difference")
                diagnostics["reason"] = "substantive_difference: aligned conditions, difference exceeds threshold"
        if not config.require_provenance or diagnostics["eligible"]:
            comparable+=1; matched += status == "matched"
        if status == "warning": warnings.append(f"{name}_mismatch")
        results.append(FieldCrosscheck(name,yv,ev,difference,ratio,status,factor,numeric_status,diagnostics))
    period_mismatch = False
    try:
        if yahoo.period_end and edinet.period_end:
            period_mismatch = abs((date.fromisoformat(yahoo.period_end[:10]) - date.fromisoformat(edinet.period_end[:10])).days) > config.period_tolerance_days
    except ValueError:
        period_mismatch = True
    if config.require_provenance:
        period_mismatch = any("period_mismatch" in f.diagnostics.get("causes",[]) for f in results)
    score=50.0 if not comparable else matched/comparable*100.0
    return CrosscheckResult(tuple(results),round(score,2),tuple(warnings),edinet_risk_flags(edinet),period_mismatch)


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
