"""Orchestration entry point for the V3 discovery-to-ranking pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from edinet_adapter import EdinetAdapter, EdinetResult
from final_ranking import FinalRankingConfig, FinalRankingResult, rank_financial_candidates
from final_report import FinalReportStats, write_final_report
from financial_crosscheck import CrosscheckResult, financial_crosscheck
from financials import (
    FinancialAdapter, FinancialConfig, FinancialResult, YahooFinanceAdapter,
    run_financial_enrichment,
)
from preselection import (
    BatchSnapshotProvider, PreselectionConfig, PreselectionResult,
    YFinanceBatchProvider, run_preselection,
)
from universe import UniverseResult, acquire_universe


@dataclass(frozen=True)
class FinalPipelineConfig:
    preselection: PreselectionConfig = PreselectionConfig(top_n=400)
    financials: FinancialConfig = FinancialConfig(max_candidates=400, top_n=75)
    ranking: FinalRankingConfig = FinalRankingConfig()
    edinet_limit: int = 50
    edinet_lookback_days: int = 190


@dataclass(frozen=True)
class FinalPipelineResult:
    universe: UniverseResult
    preselection: PreselectionResult
    financials: FinancialResult
    ranking: FinalRankingResult
    edinet_results: Mapping[str, EdinetResult]
    crosschecks: Mapping[str, CrosscheckResult]
    report_path: Path


def run_final_pipeline(
    *,
    quote_provider: BatchSnapshotProvider | None = None,
    financial_adapter: FinancialAdapter | None = None,
    edinet_adapter: EdinetAdapter | None = None,
    config: FinalPipelineConfig = FinalPipelineConfig(),
    cache_dir: str | Path = ".cache/v3-final-pipeline",
    report_path: str | Path = "v3_final_candidates_report.md",
) -> FinalPipelineResult:
    """Run bounded stages; individual quote/financial/EDINET failures remain isolated."""
    cache = Path(cache_dir)
    universe_result = acquire_universe(cache / "universe.json")
    preselection_result = run_preselection(
        universe_result.securities,
        quote_provider or YFinanceBatchProvider(),
        config=config.preselection,
        cache_path=cache / "preselection.json",
    )
    financial_result = run_financial_enrichment(
        preselection_result.top_preselected,
        financial_adapter or YahooFinanceAdapter(),
        config=config.financials,
        cache_path=cache / "financials.json",
    )

    target = financial_result.financially_ranked[:max(config.edinet_limit, 0)]
    edinet = edinet_adapter or EdinetAdapter(cache_dir=cache / "edinet")
    edinet_results: dict[str, EdinetResult] = {}
    crosschecks: dict[str, CrosscheckResult] = {}
    if not edinet.api_key:
        edinet_results.update({item.code: EdinetResult("unavailable", reason="EDINET_API_KEY is not set") for item in target})
    else:
        try:
            code_map = edinet.fetch_code_map()
            documents = edinet.find_latest_documents([item.code for item in target], lookback_days=config.edinet_lookback_days)
            for item in target:
                if item.code not in code_map:
                    result = EdinetResult("unavailable", reason="EDINET code not found")
                elif item.code not in documents:
                    result = EdinetResult("no_recent_filing", reason="useful XBRL document not found in lookback period")
                else:
                    result = edinet.fetch_document(item.code, documents[item.code])
                edinet_results[item.code] = result
                if result.data is not None:
                    crosschecks[item.code] = financial_crosscheck(item.financial_data, result.data)
        except Exception as exc:
            reason = f"EDINET batch preparation failed ({type(exc).__name__})"
            edinet_results.update({item.code: EdinetResult("error", reason=reason) for item in target})

    statuses = {code: result.status for code, result in edinet_results.items()}
    industries = {item.security.code: item.security.industry for item in preselection_result.top_preselected}
    ranking = rank_financial_candidates(
        financial_result.financially_ranked,
        crosschecks=crosschecks,
        edinet_statuses=statuses,
        industries=industries,
        config=config.ranking,
    )
    mismatch_count = sum(len(check.warnings) for check in crosschecks.values())
    missing_count = sum(item.financial_data_quality_score < 100 for item in financial_result.financially_ranked)
    stats = FinalReportStats(
        len(universe_result.securities), len(preselection_result.top_preselected),
        financial_result.stats.success_count,
        sum(result.status == "ok" for result in edinet_results.values()),
        missing_count, mismatch_count,
    )
    path = write_final_report(ranking, stats, report_path)
    return FinalPipelineResult(
        universe_result, preselection_result, financial_result, ranking,
        edinet_results, crosschecks, path,
    )


if __name__ == "__main__":
    result = run_final_pipeline()
    print(f"V3 final ranking completed: universe={len(result.universe.securities)} "
          f"preselected={len(result.preselection.top_preselected)} "
          f"financial={result.financials.stats.success_count} "
          f"edinet={sum(item.status == 'ok' for item in result.edinet_results.values())} "
          f"final={len(result.ranking.ranked_candidates)}")
