import json
from pathlib import Path
import socket
import pytest
from v3_freshness import price_freshness, age_freshness, trading_day
from v3_portfolio import analyse, analyse_portfolio, action_for, weighted_score, holdings
from v3_pipeline import Pipeline, read
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-09T08:00:00+09:00"


@pytest.mark.parametrize("now,day,status", [
    ("2026-09-06T12:00:00+09:00", "2026-09-04", "fresh"),
    ("2026-09-07T08:59:00+09:00", "2026-09-04", "fresh"),
    ("2026-09-07T15:29:59+09:00", "2026-09-04", "fresh"),
    ("2026-09-07T15:30:00+09:00", "2026-09-04", "stale"),
    ("2026-09-07T16:00:00+09:00", "2026-09-07", "fresh"),
    ("2026-09-07T10:00:00+09:00", "2026-09-07", "acceptable"),
    ("2026-09-23T16:00:00+09:00", "2026-09-18", "fresh"),
    ("2026-09-24T08:00:00+09:00", "2026-09-18", "fresh"),
    ("2027-03-23T08:00:00+09:00", "2027-03-19", "fresh"),
    ("2027-01-04T08:00:00+09:00", "2026-12-30", "fresh"),
    ("2028-03-01T16:00:00+09:00", "2028-03-01", "unknown"),
    ("2026-09-06T12:00:00+09:00", "2026-09-07", "unknown"),
    ("2026-09-06T12:00:00+09:00", "2026-09-06", "unknown"),
])
def test_calendar(now, day, status):
    result = price_freshness([day], now)
    assert result['status'] == status
    assert result['reason']
    if status == 'stale': assert '直近営業日終値' in result['warning']


def test_holidays_and_unknown():
    for value in ['2026-05-06', '2026-09-22', '2027-03-22', '2027-12-31']:
        assert not trading_day(date.fromisoformat(value))
    assert price_freshness([], NOW)['status'] == 'unknown'
    assert age_freshness(['2026-01-01'], NOW, 7)['status'] == 'stale'


def test_weights_no_tdnet_zero_penalty():
    assert weighted_score(dict(financial=60, technical=80, momentum=50, edinet=None, risk=40, event=None)) == 62
    assert weighted_score({}) is None
    assert weighted_score({'financial': 90}, {}) is None


@pytest.mark.parametrize('score,risks,tech,pnl,expected', [
    (80, [], {}, None, 'buy_more'), (60, [], {}, None, 'hold'),
    (60, [], {'rsi': 80}, None, 'take_profit_watch'),
    (80, ['negative_equity'], {}, None, 'stop_loss_watch'),
    (35, [], {}, None, 'reduce'), (None, [], {}, None, 'insufficient_data'),
    (80, [], {}, -.2, 'stop_loss_watch'), (60, [], {}, .4, 'take_profit_watch')])
def test_action(score, risks, tech, pnl, expected):
    assert action_for(score, risks, tech, pnl) == expected


def test_cost_missing_and_registered():
    data = dict(technical=dict(current_price=120, score=60, momentum_20d=0, data_as_of='2026-09-08'))
    h = dict(code='6740', shares=100)
    missing = analyse(h, data, {}, NOW)
    assert missing['cost_basis_status'] == 'missing' and missing['action'] == 'hold'
    assert missing['portfolio_score'] is not None and missing['unrealized_pnl'] is None
    known = analyse({**h, 'average_cost': 100}, data, {}, NOW)
    assert known['unrealized_pnl'] == 2000 and known['unrealized_pnl_pct'] == 20
    assert known['portfolio_score'] == missing['portfolio_score']


def test_mock_and_wrong_code_excluded():
    h = dict(code='6740', shares=100)
    assert analyse(h, {'is_mock': True, 'technical': {'score': 100}}, {}, NOW)['portfolio_score'] is None
    assert analyse(h, {'financial': {'code': '4597', 'revenue': 100}}, {}, NOW)['financial_status'] == 'unavailable'
    mocked = analyse(h, {'tdnet': {'status': 'ok', 'is_mock': True, 'events': []}}, {}, NOW)
    assert mocked['components']['event'] is None


def test_config_null_override_does_not_erase_cost(tmp_path):
    (tmp_path/'config.json').write_text(json.dumps({'portfolio':[{'code':'6740','average_cost':100}]}))
    (tmp_path/'v3_portfolio_settings.json').write_text(json.dumps({'holdings':{'6740':{'average_cost':None}}}))
    assert holdings(tmp_path)[0]['average_cost'] == 100


def test_four_outside_candidates_and_report_offline(tmp_path, monkeypatch):
    def no_network(*a, **k): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    p = Pipeline(root=ROOT, cache_dir=tmp_path/'cache', output_dir=tmp_path/'out', mode='cached', now=NOW, executor=no_network)
    state = p.run()
    assert state['status'] == 'completed'
    report = (tmp_path/'out/v3_report.md').read_text(encoding='utf-8')
    ranked = read(tmp_path/'out/v3_final_ranking.json')['ranked_candidates']
    portfolio = read(tmp_path/'out/v3_portfolio_output.json')['holdings']
    assert {h['code'] for h in portfolio} == {'6740','6573','4596','4597'}
    assert not {h['code'] for h in portfolio} & {c['code'] for c in ranked}
    assert len(ranked) == 50
    for h in portfolio:
        assert h['portfolio_score'] is not None and h['action'] != 'insufficient_data'
        assert h['components']['event'] is None and 'event' in h['missing_components']
        assert h['evaluation_reasons'] and h['financial_status'] == 'ok' and h['technical_status'] == 'ok'
        assert h['code'] in report
    for token in ['スコア内訳', '取得できなかった評価項目', '評価理由', '未登録', 'stale']:
        assert token in report


def test_no_financial_data_still_technical_analysis(tmp_path):
    items = analyse_portfolio(ROOT, tmp_path, {}, {}, {}, NOW)
    assert len(items) == 4
    p = analyse({'code':'6740'}, {'technical': {'score':55}}, {}, NOW)
    assert p['portfolio_score'] == 55 and p['action'] == 'hold'
