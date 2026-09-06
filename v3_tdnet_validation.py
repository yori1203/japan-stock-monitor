"""Offline replay of saved Yahoo/EDINET data with unavailable and mock TDnet.

Run: python v3_tdnet_validation.py --output-dir .
No acquisition, EDINET validation, or network calls are performed.
The production report uses unavailable TDnet. Mock output is stored separately.
"""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from edinet_adapter import EdinetFinancialData
from financials import FinancialCandidate, FinancialData
from financial_crosscheck import financial_crosscheck
from final_ranking import rank_financial_candidates
from final_report import FinalReportStats, write_final_report
from tdnet_adapter import TDnetAdapter, TDnetResult, MockTDnetAdapter
from tdnet_events import timestamp
from tdnet_event_report import write_tdnet_event_report


def run(output_dir=".", *, as_of=None):
    as_of = timestamp(as_of).isoformat()
    root = Path(__file__).resolve().parent
    source = json.loads((root / "validation/v3_edinet_50_input.json").read_text(encoding="utf-8"))
    state = json.loads((root / "v3_edinet_full_validation_results.json").read_text(encoding="utf-8"))
    candidates, checks, industries = [], {}, {}
    for item in source["candidates"]:
        item = dict(item)
        item["financial_data"] = FinancialData(**item["financial_data"])
        candidate = FinancialCandidate(**item)
        entry = state["entries"].get(candidate.code, {})
        candidate = replace(candidate, company_name=entry.get("company_name", candidate.company_name))
        candidates.append(candidate)
        industries[candidate.code] = entry.get("industry") or source.get("industries", {}).get(candidate.code)
        data = state["results"][candidate.code].get("data")
        if data:
            checks[candidate.code] = financial_crosscheck(candidate.financial_data, EdinetFinancialData(**data))
    statuses = {code: r["status"] for code, r in state["results"].items()}
    common = dict(crosschecks=checks, edinet_statuses=statuses, industries=industries, generated_at=as_of)
    unavailable = {c.code: TDnetAdapter().fetch_disclosures(c.code) for c in candidates}
    provider = MockTDnetAdapter(as_of=as_of)
    mock = {c.code: provider.fetch_disclosures(c.code) for c in candidates}
    baseline = rank_financial_candidates(candidates, tdnet_results=unavailable, **common)
    simulated = rank_financial_candidates(candidates, tdnet_results=mock, **common)
    neutral = rank_financial_candidates(candidates, tdnet_results={c.code: TDnetResult("mock", provider_name="mock") for c in candidates}, **common)
    stats = FinalReportStats(source["universe_count"], source["preselection_count"], len(candidates),
                            sum(s == "ok" for s in statuses.values()),
                            sum(c.financial_data_quality_score < 100 for c in candidates),
                            sum(len(c.warnings) for c in checks.values()))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_final_report(baseline, stats, out / "v3_final_candidates_report.md", generated_at=timestamp(as_of))
    write_final_report(simulated, stats, out / "v3_tdnet_mock_candidates_report.md", generated_at=timestamp(as_of))
    write_tdnet_event_report(mock, out / "v3_tdnet_event_report.md", generated_at=timestamp(as_of))
    for name, value in (("v3_final_ranking.json", asdict(baseline)), ("v3_tdnet_mock_ranking.json", asdict(simulated)),
                        ("v3_tdnet_mock_events.json", {code: asdict(r) for code, r in mock.items()})):
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    before = {c.code: c for c in baseline.ranked_candidates}
    control = {c.code: c for c in neutral.ranked_candidates}
    lines = ["# TDnet mock ranking comparison", "", "仮想イベントです。実IRではありません。同じ新配点のTDnet未照合ランキングとの比較です。", "",
             "未照合時は90点分で再正規化、取得済み中立時はTDnet=50を含む100点分で計算します。増配などの好材料でも未照合時より総合点が低くなる場合があります。neutral deltaは同じ取得状態での材料効果です。", "",
             "配点変更前のEDINET版からの変動と混同しないよう、baselineも新配点で計算しています。", "",
             "| code | event | baseline rank | mock rank | baseline score | mock score | delta | neutral delta |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for c in simulated.ranked_candidates:
        if mock[c.code].events:
            b = before[c.code]
            lines.append(f"| {c.code} | {mock[c.code].events[0].event_type.value} | {b.rank} | {c.rank} | {b.final_score:.2f} | {c.final_score:.2f} | {c.final_score-b.final_score:+.2f} | {c.final_score-control[c.code].final_score:+.2f} |")
    (out / "v3_tdnet_mock_comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return baseline, simulated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--as-of", help="Optional fixed evaluation timestamp for reproducible replay")
    args = parser.parse_args()
    run(args.output_dir, as_of=args.as_of)
