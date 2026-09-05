from datetime import datetime, timezone

from final_ranking import rank_final_candidates
from final_report import FinalReportStats, build_final_report, write_final_report
from tests.test_final_ranking import candidate


def test_report_contains_required_sections(tmp_path):
    result = rank_final_candidates([candidate("1000"), candidate("1001", minimum_purchase_amount=20_000)])
    stats = FinalReportStats(3707, 400, 75, 50, 2, 1)
    text = build_final_report(result, stats, generated_at=datetime(2026, 9, 5, tzinfo=timezone.utc))
    for expected in ("実行日時 JST", "対象ユニバース数: 3707", "Top 20", "Top 10",
                     "5万円以下 Top 10", "カテゴリ数", "Yahoo/EDINET不一致件数"):
        assert expected in text
    assert "1000" in text and "final_score" in text
    generated = datetime(2026, 9, 5, tzinfo=timezone.utc)
    path = write_final_report(result, stats, tmp_path / "report.md", generated_at=generated)
    assert path.read_text(encoding="utf-8") == build_final_report(result, stats, generated_at=generated)


def test_empty_report_is_safe():
    result = rank_final_candidates([])
    text = build_final_report(result, FinalReportStats(0, 0, 0, 0))
    assert "最終候補数: 0" in text
