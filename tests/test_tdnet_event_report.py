from tdnet_adapter import MockTDnetAdapter, TDnetAdapter
from tdnet_event_report import build_tdnet_event_report, write_tdnet_event_report
from final_report import build_final_report, FinalReportStats
from final_ranking import rank_final_candidates
from tests.test_final_ranking import candidate
from tests.test_tdnet_events import event, NOW


def test_report_counts_status_and_generation(tmp_path):
    provider = MockTDnetAdapter()
    results = {code: provider.fetch_disclosures(code) for code in ("2146", "8614", "2317", "8572", "8616", "8410", "4406")}
    results["0000"] = TDnetAdapter().fetch_disclosures("0000")
    text = build_tdnet_event_report(results, generated_at=NOW)
    for required in ("MOCK", "実行日時 JST", "対象銘柄数: 8", "event取得数: 7", "上方修正件数: 1", "下方修正件数: 1", "希薄化件数: 2", "増配件数: 1", "自社株買い件数: 1", "その他重要IR件数: 1", "positive top events", "negative top events", "TDnet未照合"):
        assert required in text
    assert write_tdnet_event_report(results, tmp_path / "report.md", generated_at=NOW).read_text(encoding="utf-8") == text


def test_final_report_integration():
    result = rank_final_candidates([candidate(tdnet_status="mock", tdnet_events=(event("増配"),)), candidate("2000")], generated_at=NOW)
    text = build_final_report(result, FinalReportStats(2, 2, 2, 2))
    for required in ("TDnet status", "tdnet_event_score", "直近重要イベント", "positive_flags", "risk_flags", "TDnet未照合", "仮想イベント"):
        assert required in text


def test_empty_report():
    assert "event取得数: 0" in build_tdnet_event_report({}, generated_at=NOW)


def test_offline_replay_keeps_mock_out_of_production(tmp_path, monkeypatch):
    import socket
    from v3_tdnet_validation import run
    def reject_network(*args, **kwargs):
        raise AssertionError("offline replay attempted network access")
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    baseline, simulated = run(tmp_path, as_of=NOW)
    assert len(baseline.ranked_candidates) == len(simulated.ranked_candidates) == 50
    assert all(c.tdnet_status == "unavailable" and not c.recent_events for c in baseline.ranked_candidates)
    assert sum(bool(c.recent_events) for c in simulated.ranked_candidates) == 7
    assert "neutral delta" in (tmp_path / "v3_tdnet_mock_comparison.md").read_text(encoding="utf-8")
