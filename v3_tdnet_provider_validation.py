"""Permission-gated smoke and ranking replay; default makes zero network requests.

Existing financial results are reused. No paid contracts or permission are
assumed. Source policy research is recorded separately from successful ingestion.
"""
import argparse
from dataclasses import asdict, fields, replace
import json
from pathlib import Path
from final_ranking import RankingCandidate, rank_final_candidates
from tdnet_events import timestamp, JST
from tdnet_provider import FreePublicTDnetProvider, production_results
from tdnet_provider_report import write_provider_report


def run(output_dir=".", *, target_date=None):
    root = Path(__file__).resolve().parent
    saved = json.loads((root / "v3_final_ranking.json").read_text(encoding="utf-8"))["ranked_candidates"]
    allowed = {f.name for f in fields(RankingCandidate)}
    candidates = [RankingCandidate(**{k: v for k, v in row.items() if k in allowed}) for row in saved]
    codes = [c.code for c in candidates]
    now = timestamp()
    day = target_date or str(now.astimezone(JST).date())
    result = FreePublicTDnetProvider().fetch_events_for_codes(codes, dates=[day])
    mapped = production_results(result, codes)
    updated = [replace(c, tdnet_status=mapped[c.code].status, tdnet_events=mapped[c.code].events,
                       tdnet_provider=mapped[c.code].provider_name) for c in candidates]
    ranking = rank_final_candidates(updated, generated_at=now.isoformat())
    baseline = rank_final_candidates([replace(c, tdnet_status="unavailable", tdnet_events=()) for c in candidates], generated_at=now.isoformat())
    before = {c.code: c for c in baseline.ranked_candidates}
    changes = [c for c in ranking.ranked_candidates if c.final_score != before[c.code].final_score or c.rank != before[c.code].rank]
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_provider_report(result, out / "v3_tdnet_events_report.md", ranking_changes=[
        f"- 保存済み候補数: {len(candidates)}（Yahoo/EDINETの再取得なし）",
        f"- TDnetに起因するスコア・順位変化: {len(changes)}銘柄",
        "- production_results経由で完全取得・実データのみを受け渡し。mock・partial・unavailable・errorは除外。",
        "- 実通信の追加試行なし。前セッションで公式一覧1ページと免責事項1ファイルを条件確認のため閲覧済み。実イベント取込成功には算入しません。",
    ])
    (out / "v3_tdnet_provider_validation.json").write_text(json.dumps({
        "status": result.status, "source": result.source, "is_mock": result.is_mock,
        "fetched_at": result.fetched_at, "events": len(result.events), "cache_hits": result.cache_hits,
        "requests": result.request_count, "failures": result.failure_count, "candidate_count": len(candidates),
        "ranking_changes": len(changes), "warnings": result.warnings}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={result.status} events={len(result.events)} requests={result.request_count} cache_hits={result.cache_hits} failures={result.failure_count} ranking_changes={len(changes)}")
    return result, ranking


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--date")
    args = parser.parse_args()
    run(args.output_dir, target_date=args.date)
