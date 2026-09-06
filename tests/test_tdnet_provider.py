"""Synthetic HTML/transport only. No source content or network in these tests."""
from dataclasses import replace
import json
import pytest
from tdnet_events import normalize_event, timestamp
from tdnet_provider import (AccessConfig, FreePublicTDnetProvider, MockTDnetProvider,
    ProviderResult, SOURCE, create_provider, parse_public_page, production_results)
from final_ranking import score_final_candidate, rank_financial_candidates
from tests.test_final_ranking import candidate
from financials import FinancialCandidate, FinancialData
from tdnet_adapter import TDnetResult

DAY = "2026-09-06"
NOW = "2026-09-06T12:00:00+09:00"


def html(title="業績予想の上方修正", *, rows=1, page2=False):
    row = f'<tr><td class="kjTime">10:00</td><td class="kjCode">10000</td><td class="kjName">テスト株式会社</td><td class="kjTitle"><a href="140120260906000001.pdf">{title}</a></td></tr>'
    return ('<html><title>適時開示情報閲覧サービス</title><div>2026年09月06日</div>'
            + f'<div>全{rows}件</div><table>' + row * rows + '</table>'
            + ('<div onclick="pagerLink(\'I_list_002_20260906.html\')">2</div>' if page2 else '') + '</html>')


class Clock:
    def __init__(self): self.value = timestamp(NOW).timestamp(); self.waits = []
    def now(self): return self.value
    def sleep(self, value): self.waits.append(value); self.value += value


def provider(tmp_path, responses=None, **options):
    clock = Clock()
    calls = []
    responses = iter(responses if responses is not None else [(200, html(), {})])
    def transport(url, timeout):
        calls.append((url, timeout))
        item = next(responses)
        if isinstance(item, Exception): raise item
        return item
    p = FreePublicTDnetProvider(tmp_path, permission_reference="SYNTHETIC-TEST-ONLY", transport=transport,
                                now=clock.now, sleep=clock.sleep, **options)
    return p, calls, clock


def test_switch_and_permission_gate_never_network(tmp_path):
    def fail(*args): raise AssertionError("network prohibited")
    p = create_provider("free_public", cache_dir=tmp_path, transport=fail)
    r = p.fetch_events_for_date(DAY)
    assert r.status == "unavailable" and r.request_count == 0 and not list(tmp_path.iterdir())
    assert create_provider("mock").fetch_events_for_date(DAY).is_mock
    with pytest.raises(ValueError): create_provider("jquants")


def test_success_normalization_and_reuse(tmp_path):
    p, calls, clock = provider(tmp_path)
    r = p.fetch_events_for_codes(["1000"], dates=[DAY])
    assert r.status == "ok" and r.success_count == 1 and len(r.events) == 1
    e = r.events[0]
    assert e.code == "1000" and e.company_name == "テスト株式会社"
    assert e.source == SOURCE and e.is_mock is False and e.fetched_at
    assert e.event_type.value == "upward_revision" and e.category is None
    again = p.fetch_events_for_date(DAY)
    assert len(calls) == 1 and again.cache_hits == 1 and again.request_count == 0
    assert again.events[0].fetched_at == e.fetched_at
    assert len([f for f in tmp_path.glob('*.json') if len(f.stem) == 64]) == 1


def test_duplicate_identifier(tmp_path):
    p, _, _ = provider(tmp_path, [(200, html(rows=2), {})])
    assert len(p.fetch_events_for_date(DAY).events) == 1


def test_successful_empty_distinct_from_changed_html(tmp_path):
    p, _, _ = provider(tmp_path / "empty", [(200, html(rows=0), {})])
    assert p.fetch_events_for_date(DAY).status == "ok"
    p, _, _ = provider(tmp_path / "changed", [(200, '<html>login</html>', {})])
    r = p.fetch_events_for_date(DAY)
    assert r.status == "error" and r.failure_count == 1


@pytest.mark.parametrize("status", [403, 429])
def test_no_retry_and_persisted_cooldown(tmp_path, status):
    p, calls, clock = provider(tmp_path, [(status, "", {"Retry-After": "7200"})])
    assert p.fetch_events_for_date(DAY).status == "error"
    assert p.fetch_events_for_date(DAY).status == "unavailable"
    assert len(calls) == 1 and not clock.waits
    assert json.loads((tmp_path / "access.json").read_text())["cooldown_until"] > clock.now()


@pytest.mark.parametrize("response", [(500, "", {}), TimeoutError("timeout")])
def test_bounded_retry_and_backoff(tmp_path, response):
    p, calls, clock = provider(tmp_path, [response, (200, html(), {})])
    r = p.fetch_events_for_date(DAY)
    assert r.status == "ok" and r.failure_count == 1 and r.request_count == 2
    assert clock.waits == [10.] and calls[0][1] == 10


def test_exponential_retry_exhaustion(tmp_path):
    p, calls, clock = provider(tmp_path, [(500, "", {})] * 3,
                                config=AccessConfig(retries=2, max_requests=3))
    r = p.fetch_events_for_date(DAY)
    assert r.status == "error" and len(calls) == 3 and clock.waits == [10., 20.]


def test_pagination_partial_and_resume(tmp_path):
    p, calls, _ = provider(tmp_path, [(200, html(page2=True), {})])
    r = p.fetch_events_for_date(DAY)
    assert r.status == "partial" and len(calls) == 1
    assert production_results(r, ["1000"])["1000"].status == "unavailable"
    p2, calls2, _ = provider(tmp_path, [(200, html(), {})], config=AccessConfig(max_pages=2))
    r2 = p2.fetch_events_for_date(DAY)
    assert r2.status == "ok" and r2.cache_hits == 1 and len(calls2) == 1 and len(r2.events) == 1


def test_missing_row_and_deleted_title(tmp_path):
    broken = html(rows=2).replace('<a href="140120260906000001.pdf">', '<a>', 1)
    p, _, _ = provider(tmp_path, [(200, broken, {})])
    r = p.fetch_events_for_date(DAY)
    assert r.status == "partial" and r.warnings and len(r.events) == 1


def test_corrupt_cache_and_lock_fail_closed(tmp_path):
    p, calls, _ = provider(tmp_path)
    (tmp_path / f"{DAY}-001.json").write_text("broken")
    assert p.fetch_events_for_date(DAY).status == "error" and not calls
    (tmp_path / "fetch.lock").write_text("other process")
    assert p.fetch_events_for_date(DAY).status == "unavailable"


@pytest.mark.parametrize("title,kind", [("下方修正", "downward_revision"), ("増配", "dividend_increase"),
    ("減配", "dividend_decrease"), ("自己株式取得", "share_buyback"), ("MSワラント", "warrant"),
    ("資本業務提携", "capital_alliance"), ("ストックオプション", "stock_option")])
def test_classification_and_ranking(tmp_path, title, kind):
    p, _, _ = provider(tmp_path, [(200, html(title), {})])
    r = p.fetch_events_for_date(DAY)
    mapped = production_results(r, ["1000"])["1000"]
    assert mapped.events[0].event_type.value == kind
    neutral = score_final_candidate(candidate(tdnet_status="ok"), generated_at=NOW)
    scored = score_final_candidate(candidate(tdnet_status="ok", tdnet_events=mapped.events), generated_at=NOW)
    assert (scored.final_score > neutral.final_score) == (kind in ("dividend_increase", "share_buyback", "capital_alliance"))


def test_mock_contamination_and_coverage_rejected():
    result = MockTDnetProvider().fetch_events_for_date(DAY)
    assert production_results(result, ["2146"])["2146"].status == "unavailable"
    disguised = replace(result, source=SOURCE, is_mock=False)
    assert production_results(disguised, ["2146"])["2146"].status == "unavailable"
    restricted = ProviderResult("ok", source=SOURCE, codes=("1000",))
    assert production_results(restricted, ["2000"])["2000"].status == "unavailable"
    fake = normalize_event(dict(code="1000", title="増配", published_at=NOW, source=SOURCE, is_mock=True))
    scored = score_final_candidate(candidate(tdnet_status="ok", tdnet_events=(fake,)), generated_at=NOW)
    assert scored.tdnet_event_score is None


def test_financial_production_default_rejects_mock():
    c = FinancialCandidate("2146", "test", "Prime", 30000, 70, 80, 75, 70, 65, 85, 50, 90,
                           (), (), NOW, FinancialData("2146"))
    mock = MockTDnetProvider().fetch_events_for_date(DAY)
    results = {"2146": TDnetResult("ok", mock.events, provider_name=SOURCE)}
    ranked = rank_financial_candidates([c], tdnet_results=results, generated_at=NOW)
    assert ranked.ranked_candidates[0].tdnet_event_score is None


def test_unavailable_renormalization():
    mapped = production_results(ProviderResult("unavailable"), ["1000"])["1000"]
    scored = score_final_candidate(candidate(tdnet_status=mapped.status), generated_at=NOW)
    assert scored.final_score == score_final_candidate(candidate(), generated_at=NOW).final_score


def test_out_of_range_invalid_date_no_network(tmp_path):
    p, calls, _ = provider(tmp_path)
    assert p.fetch_events_for_date("2020-01-01").status == "unavailable"
    assert p.fetch_events_for_date("bad").status == "unavailable"
    assert not calls


def test_report_and_smoke_without_network(tmp_path, monkeypatch):
    import socket
    from v3_tdnet_provider_validation import run
    monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("network attempted"))
    result, ranking = run(tmp_path, target_date=DAY)
    assert result.status == "unavailable" and len(ranking.ranked_candidates) == 50
    text = (tmp_path / "v3_tdnet_events_report.md").read_text(encoding="utf-8")
    assert "cache hit" in text and "スコア・順位変化: 0銘柄" in text and "利用許諾" in text
