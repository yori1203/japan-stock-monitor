from dataclasses import asdict
import json
from pathlib import Path
import socket
import pytest
from v3_pipeline import Pipeline, Paused, STAGES, read, digest
from v3_edinet_full_validation import atomic_json
from v3_pipeline_report import freshness, portfolio_review, ranking_changes
from tests.test_final_ranking import candidate
from final_ranking import score_final_candidate

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-07T06:00:00+09:00"


@pytest.fixture
def no_network(monkeypatch):
    def fail(*args, **kwargs): raise AssertionError("network attempted")
    monkeypatch.setattr(socket.socket, "connect", fail)
    return fail


def pipeline(tmp_path, **kwargs):
    return Pipeline(root=ROOT, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out", now=NOW, **kwargs)


@pytest.mark.parametrize("mode", ["cached", "quick"])
def test_modes_checkpoint_edinet_and_reports(tmp_path, no_network, mode):
    p = pipeline(tmp_path, mode=mode, executor=no_network)
    state = p.run()
    assert state["status"] == "completed"
    assert tuple(state["stages"]) == STAGES
    for stage in state["stages"].values():
        assert all(k in stage for k in ("status", "started_at", "completed_at", "input_count", "success_count", "failure_count", "cache_hits"))
        assert stage["started_at"] and stage["completed_at"]
    assert state["edinet_counts"] == {"ok": 49, "no_recent_filing": 1}
    assert state["stages"]["edinet"]["failure_count"] == 0
    assert state["stages"]["edinet"]["cache_hits"] == 50
    assert state["tdnet_status"] == "unavailable"
    ranked = read(tmp_path / "out/v3_final_ranking.json")
    assert len(ranked["ranked_candidates"]) == 50 and len(ranked["top_20"]) == 20
    assert all(c["tdnet_event_score"] is None and c["tdnet_status"] == "unavailable" for c in ranked["ranked_candidates"])
    assert all(c["minimum_purchase_amount"] <= 50000 for c in ranked["small_investment_top_10"])
    report = (tmp_path / "out/v3_report.md").read_text(encoding="utf-8")
    for token in ("data freshness", "保有株評価", "1万円前後", "A評価", "前回ランキング", "no_recent_filing", "risk_flags", "positive_flags"):
        assert token in report
    if mode == "quick": assert state["stages"]["universe"]["status"] == "skipped"


def test_resume_only_failed_stage_and_downstream(tmp_path, no_network):
    visited = []
    def interrupt(stage):
        visited.append(stage)
        if stage == "ranking": raise RuntimeError("synthetic stop")
    first = pipeline(tmp_path, mode="quick", before_stage=interrupt).run()
    assert first["status"] == "failed" and first["stages"]["financial"]["status"] == "cached"
    completed_time = first["stages"]["financial"]["completed_at"]
    visited.clear()
    final = pipeline(tmp_path, mode="quick", before_stage=visited.append).run()
    assert visited == ["ranking", "report"] and final["status"] == "completed"
    assert final["stages"]["financial"]["completed_at"] == completed_time
    visited.clear()
    pipeline(tmp_path, mode="quick", before_stage=visited.append).run()
    assert visited == []


def test_missing_report_rebuilt_without_upstream(tmp_path, no_network):
    pipeline(tmp_path, mode="quick").run()
    (tmp_path / "out/v3_report.md").unlink()
    visited = []
    state = pipeline(tmp_path, mode="quick", before_stage=visited.append).run()
    assert state["status"] == "completed" and visited == ["report"]


def test_artifact_corruption_fails_without_refetch(tmp_path, no_network):
    p = pipeline(tmp_path, mode="quick")
    p.run()
    p.artifact("financial").write_text('{}')
    with pytest.raises(ValueError): pipeline(tmp_path, mode="quick", executor=no_network).run()


def test_freshness_old_missing_future():
    assert freshness(["2020-01-01"], NOW, 7)["warning"] == "古いデータを含む"
    assert freshness([], NOW, 7)["oldest"] is None
    assert freshness(["2099-01-01"], NOW, 7)["warning"] == "未来日時を含む"


def test_portfolio_all_assessments_and_missing():
    base = asdict(score_final_candidate(candidate()))
    items = [{**base, "code": "1000", "final_score": 85, "positive_flags": ["upward_revision"]},
             {**base, "code": "2000", "final_score": 65},
             {**base, "code": "3000", "risk_flags": ["major_dilution"]},
             {**base, "code": "4000"}]
    holdings = [{"code": code, "shares": 100, "average_cost": 100} for code in ("1000", "2000", "3000", "4000", "5000")]
    snapshots = {code: {"current_price": 140, "data_as_of": "2026-09-04"} for code in ("1000", "2000", "3000", "4000", "5000")}
    result = portfolio_review(holdings, items, snapshots)
    assert [p["assessment"] for p in result] == ["買い増し候補", "利確警戒", "損切り警戒", "保有継続", "評価保留"]
    assert portfolio_review([{"code": "1000"}], [], {})[0]["missing"] == ["財務・総合スコア", "取得単価", "株価"]


def test_ranking_changes_and_new_entries():
    old = [{"code": "A", "rank": 1}, {"code": "B", "rank": 21}, {"code": "D", "rank": 3}]
    new = [{"code": "B", "rank": 1}, {"code": "A", "rank": 2}, {"code": "C", "rank": 3}]
    result = ranking_changes(new, old)
    assert result == {"up": ["B"], "down": ["A"], "new": ["C"], "top20_new": ["B", "C"], "removed": ["D"]}


def test_mock_contamination_cannot_enter_production(tmp_path, no_network):
    p = pipeline(tmp_path, mode="quick")
    p.run()
    raw = p.result("tdnet")
    from tdnet_events import normalize_event
    e = normalize_event({"code": "8614", "title": "増配", "published_at": NOW, "is_mock": True})
    raw["results"]["8614"]["status"] = "ok"
    raw["results"]["8614"]["events"] = [json.loads(json.dumps(asdict(e), default=str))]
    atomic_json(p.artifact("tdnet"), raw)
    p.state["stages"]["tdnet"]["output_hash"] = digest(raw)
    p.state["stages"]["ranking"]["status"] = "pending"
    p.state["stages"]["report"]["status"] = "pending"
    p.save()
    assert pipeline(tmp_path, mode="quick").run()["status"] == "completed"
    c = next(c for c in read(tmp_path / "out/v3_final_ranking.json")["ranked_candidates"] if c["code"] == "8614")
    assert c["tdnet_event_score"] is None


def test_full_budget_pause_and_resume_reuses_finished_universe(tmp_path, no_network):
    # Tiny synthetic full run: existing engine objects; transport replaced entirely.
    root = tmp_path / "root"
    (root / "validation").mkdir(parents=True)
    frozen = read(ROOT / "validation/v3_edinet_50_input.json")
    atomic_json(root / "validation/v3_edinet_50_input.json", frozen)
    atomic_json(root / "v3_edinet_full_validation_results.json", read(ROOT / "v3_edinet_full_validation_results.json"))
    security = dict(code="8614", company_name="synthetic", market="Prime", industry="test", trading_unit=100,
                    source="synthetic", fetched_at=NOW, security_type="common_stock")
    quote = dict(code="8614", current_price=742, average_volume=100000, average_trading_value=74200000,
                 price_change_20d=.1, price_change_60d=.2, ma20=700, ma60=650,
                 distance_from_52w_high=-.1, zero_volume_ratio=0, daily_volatility=.02,
                 observation_count=200, data_as_of=NOW, source="synthetic")
    calls = []
    def transport(op, arg):
        calls.append(op)
        if op == "universe": return [{**security, "fetched_at": NOW}]
        if op == "quotes": return {"8614": {**quote, "data_as_of": NOW}}
        raise AssertionError(op)
    options = dict(root=root, cache_dir=tmp_path / "cache", output_dir=tmp_path / "out", mode="full", now=NOW, executor=transport)
    assert Pipeline(**options, max_operations=1).run()["status"] == "paused"
    assert calls == ["universe"]
    final = Pipeline(**options).run()
    assert final["status"] == "completed" and calls == ["universe", "quotes"]
    cached_options = {**options, "mode": "cached", "executor": no_network}
    reused = Pipeline(**cached_options).run()
    assert reused["status"] == "completed" and reused["stages"]["financial"]["success_count"] == 1


def test_lock_prevents_parallel_run(tmp_path):
    p = pipeline(tmp_path)
    (p.cache / "pipeline.lock").write_text("another run")
    with pytest.raises(RuntimeError): p.run()
