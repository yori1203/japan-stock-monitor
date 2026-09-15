import csv
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import v3_operations as ops

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 15, 8, 30, tzinfo=ops.monitor.JST)


@pytest.fixture
def services(monkeypatch):
    calls = []
    def analyse(code, category, priority):
        calls.append(code)
        return dict(code=code, category=category, priority=priority, price=100, score=50,
                    signal="監視", signal_key="hold", confidence="低", rsi=50, ma5=100, ma25=100,
                    ma75=100, volume_ratio=1, reasons=[], fetched_at=NOW, data_as_of="2026-09-14",
                    source="Yahoo Finance")
    monkeypatch.setattr(ops.monitor, "analyse", analyse)
    monkeypatch.setattr(ops.backtest, "download_stock", lambda *a, **kw: SimpleNamespace(frame=None))
    monkeypatch.setattr(ops.backtest, "simulate", lambda *a: [])
    def pipeline(root, output):
        items = [dict(code=str(8000 + i), rank=i + 1, final_score=90 - i,
                      company_name="fixture", edinet_status="ok") for i in range(5)]
        (output / "v3_final_ranking.json").write_text(json.dumps({"ranked_candidates": items}), encoding="utf-8")
        return {"status": "completed", "stages": {}, "edinet_counts": {"ok": 48, "no_recent_filing": 2},
                "crosscheck_counts": {"not_comparable": 200, "unavailable": 88}}
    return calls, analyse, pipeline


@pytest.mark.parametrize("session", ["morning", "noon", "evening"])
def test_complete_sessions_preserve_top5_and_deduplicate(tmp_path, services, session):
    calls, _, pipeline = services
    for _ in range(2):
        assert ops.run(session, ROOT, tmp_path, now=NOW, pipeline_runner=pipeline) == 0
    assert set(calls[:9]) == set.union(*ops.EXPECTED.values())
    status = json.loads((tmp_path / "operations_status.json").read_text(encoding="utf-8"))
    assert status["top5"] == [str(8000+i) for i in range(5)]
    assert status["complete"] and not status["issues"]
    text = (tmp_path / f"report_{session}.md").read_text(encoding="utf-8")
    assert text == (tmp_path / "report.md").read_text(encoding="utf-8")
    assert f"session: {session}" in text and "リアルタイム価格ではありません" in text
    assert "2026-09-14" in text and "not_comparable" in text and "no_recent_filing" in text
    assert (tmp_path / "backtest_report.md").exists()
    with (tmp_path / "signals.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 14 and {r["session"] for r in rows} == {session}


def test_one_missing_stock_is_failure_but_remaining_stocks_are_processed(tmp_path, services, monkeypatch):
    calls, analyse, pipeline = services
    def fail_one(code, *args):
        if code == "6740":
            calls.append(code)
            raise RuntimeError("secret-must-not-appear")
        return analyse(code, *args)
    monkeypatch.setattr(ops.monitor, "analyse", fail_one)
    assert ops.run("morning", ROOT, tmp_path, now=NOW, pipeline_runner=pipeline) == 1
    assert set(calls[:9]) == set.union(*ops.EXPECTED.values())
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "secret-must-not-appear" not in text and "system_error" in text and "6740" in text


def test_incomplete_pipeline_does_not_publish_old_top5(tmp_path, services):
    services[2](ROOT, tmp_path)
    assert ops.run("noon", ROOT, tmp_path, now=NOW,
                   pipeline_runner=lambda *args: {"status": "paused"}) == 1
    status = json.loads((tmp_path / "operations_status.json").read_text(encoding="utf-8"))
    assert status["top5"] == [] and not status["complete"]


def test_signals_key_ignores_category_and_preserves_other_sessions(tmp_path):
    rows = [dict(date="2026-09-15 08:30", session="morning", code="6740", category="portfolio"),
            dict(date="2026-09-15 08:31", session="morning", code="6740", category="discovery"),
            dict(date="2026-09-15 12:30", session="noon", code="6740", category="portfolio")]
    ops.save_signals(tmp_path / "signals.csv", rows)
    assert len(ops._read_rows(tmp_path / "signals.csv")) == 2


def test_backtest_failure_is_not_normal_completion(tmp_path, services, monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("sensitive-provider-message")
    monkeypatch.setattr(ops.backtest, "download_stock", failed)
    assert ops.run("evening", ROOT, tmp_path, now=NOW, pipeline_runner=services[2]) == 1
    assert "sensitive-provider-message" not in (tmp_path / "backtest_report.md").read_text(encoding="utf-8")


def test_workflow_schedule_and_isolated_storage():
    text = (ROOT / ".github/workflows/v3-operations.yml").read_text(encoding="utf-8")
    assert "30 23 * * 0-4" in text  # previous UTC day, Sunday-Thursday
    assert "30 3 * * 1-5" in text and "0 7 * * 1-5" in text
    assert "ref: feature/v3-discovery-engine" in text
    assert "HEAD:refs/heads/v3-operation-data" in text
    assert "actions/upload-artifact" in text and "state/*.csv" in text
