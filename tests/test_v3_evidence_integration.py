"""Downstream integration gates; stage-1 comparison implementation is frozen."""
from dataclasses import asdict, replace
from pathlib import Path
import socket
import pytest
from financials import FinancialCandidate, FinancialData
from edinet_adapter import EdinetFinancialData
from financial_crosscheck import CrosscheckConfig, financial_crosscheck
from final_ranking import from_financial_candidate, score_final_candidate
from v3_pipeline import Pipeline, read
from v3_portfolio import analyse

ROOT=Path(__file__).resolve().parents[1]
NOW='2026-09-13T13:30:00+09:00'

def financial(y):
    return FinancialCandidate('1000','Example','Prime',30000,70,80,75,70,65,85,50,90,
        (),('edinet_values_confirmed',),NOW,y)

@pytest.mark.parametrize('value',[1000,None])
def test_incomparable_or_missing_equals_no_edinet_weight(value):
    y=FinancialData('1000',revenue=1000)
    check=financial_crosscheck(y,EdinetFinancialData('1000',revenue=value),CrosscheckConfig(require_provenance=True))
    # A stale aggregate score cannot override field evidence.
    check=replace(check,crosscheck_score=100)
    c=financial(y)
    got=score_final_candidate(from_financial_candidate(c,crosscheck=check,edinet_status='ok'))
    absent=score_final_candidate(from_financial_candidate(c,edinet_status='unavailable'))
    assert got.crosscheck_score is None
    assert got.final_score==absent.final_score
    assert not any('EDINET' in reason for reason in got.score_reasons)
    assert 'EDINET比較条件未確認・照合対象不足（加点なし）' in got.warning_reasons

def test_only_eligible_fields_affect_score():
    m={'period_start':'2025-04-01','period_end':'2026-03-31','scope':'consolidated','original_unit':'JPY'}
    y=FinancialData('1000',revenue=1000,net_income=1000,field_metadata={'revenue':m})
    e=EdinetFinancialData('1000',revenue=500,net_income=1000,field_metadata={'revenue':{**m,'tag':'{test}NetSales'}})
    check=financial_crosscheck(y,e,CrosscheckConfig(require_provenance=True))
    ranked=from_financial_candidate(financial(y),crosscheck=check,edinet_status='ok')
    assert ranked.crosscheck_score==0  # One verified difference; unverified equality excluded.
    assert 'revenue_mismatch' in ranked.risk_flags
    assert 'net_income_mismatch' not in ranked.risk_flags
    verified=financial_crosscheck(y,replace(e,revenue=1000),CrosscheckConfig(require_provenance=True))
    assert from_financial_candidate(financial(y),crosscheck=verified,edinet_status='ok').crosscheck_score==100

def test_missing_status_rejects_even_stale_perfect_check():
    y=FinancialData('1000',revenue=1000)
    check=financial_crosscheck(y,EdinetFinancialData('1000',revenue=1000))
    c=from_financial_candidate(financial(y),crosscheck=check,edinet_status='no_recent_filing')
    assert c.crosscheck_score is None
    assert not any('EDINET' in s for s in score_final_candidate(c).score_reasons)

def test_portfolio_uses_same_gate():
    y=FinancialData('1000',revenue=1000,equity=500,total_assets=1000,fetched_at=NOW)
    raw={'financial':asdict(y),'edinet':{'status':'ok','data':asdict(EdinetFinancialData('1000',revenue=1000,equity=500,total_assets=1000))}}
    got=analyse({'code':'1000','shares':100},raw,{},NOW)
    absent=analyse({'code':'1000','shares':100},{'financial':asdict(y)},{},NOW)
    assert got['components']['edinet'] is None
    assert got['portfolio_score']==absent['portfolio_score']
    assert got['available_weight']==absent['available_weight']


def test_pipeline_rejects_old_extraction_cache_and_accepts_current(tmp_path):
    p=Pipeline(root=ROOT,cache_dir=tmp_path/'cache',output_dir=tmp_path/'out',now=NOW)
    code=p.source['candidates'][0]['code']
    old=dict(p.saved_ed['results'][code])
    assert old['status']=='ok'
    # Isolate this candidate and provide current extractor provenance.
    p.source={**p.source,'candidates':[p.source['candidates'][0]]}
    raw=dict(old['data'])
    raw['field_metadata']={'revenue':{'extraction_version':5}}
    p.saved_ed={**p.saved_ed,'results':{code:{**old,'data':raw}}}
    assert p.run()['status']=='completed'
    assert p.state['edinet_counts']=={'ok':1}
    ranked=read(tmp_path/'out/v3_final_ranking.json')['ranked_candidates']
    assert ranked[0]['crosscheck_score'] is None  # Cache validity isn't matching proof.


def test_full_report_assesses_after_acquisition_and_freezes_reference(tmp_path,monkeypatch):
    import v3_pipeline as pipeline_module
    from v3_pipeline import digest
    from v3_edinet_full_validation import atomic_json
    p=Pipeline(root=ROOT,cache_dir=tmp_path/'cache',output_dir=tmp_path/'out',now=NOW)
    assert p.run()['status']=='completed'
    # Simulate acquisition finishing after this run's start, without network.
    acquired='2026-09-13T13:35:00+09:00'
    assessed='2026-09-13T13:40:00+09:00'
    financial=p.result('financial')
    for c in financial['candidates']:c['fetched_at']=acquired
    atomic_json(p.artifact('financial'),financial)
    p.state['stages']['financial']['output_hash']=digest(financial)
    original_timestamp=pipeline_module.timestamp
    monkeypatch.setattr(pipeline_module,'timestamp',lambda value=None:original_timestamp(value or assessed))
    p.mode='full';p.state['mode']='full'
    p.stage_report()
    summary=read(tmp_path/'out/v3_pipeline_summary.json')
    assert summary['freshness']['fundamentals']['status']=='fresh'
    first=p.state['assessment_at']
    assessed='2026-09-15T13:40:00+09:00'
    assert p.assessment_time().isoformat()==first
    # A real future timestamp must still be rejected by freshness evaluation.
    for c in financial['candidates']:c['fetched_at']='2026-10-01T00:00:00+09:00'
    atomic_json(p.artifact('financial'),financial)
    p.state['stages']['financial']['output_hash']=digest(financial)
    p.stage_report()
    assert read(tmp_path/'out/v3_pipeline_summary.json')['freshness']['fundamentals']['status']=='unknown'

@pytest.mark.parametrize('mode',['cached','quick'])
def test_existing_pipeline_outputs_exclude_unverified_bonus(tmp_path,monkeypatch,mode):
    def forbidden(*a,**kw):raise AssertionError('external acquisition forbidden')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    args=dict(root=ROOT,mode=mode,cache_dir=tmp_path/'cache',output_dir=tmp_path/'out',now=NOW,executor=forbidden)
    p=Pipeline(**args);state=p.run()
    assert state['status']=='completed'
    ranked=read(tmp_path/'out/v3_final_ranking.json')['ranked_candidates']
    assert len(ranked)==50
    assert all(c['crosscheck_score'] is None for c in ranked)
    assert state['edinet_counts']=={'no_recent_filing':1,'unavailable':49}
    assert not p.result('edinet')['entries'].get('3660')
    assert not any('一致度が高い' in s or 'EDINET公式値で確認済み' in s for c in ranked for s in c['score_reasons'])
    for name in ('v3_report.md','v3_final_candidates_report.md'):
        text=(tmp_path/'out'/name).read_text(encoding='utf-8')
        assert 'not_comparable' in text
        assert 'Yahoo/EDINET一致度が高い' not in text
    original=(tmp_path/'out/v3_final_ranking.json').read_bytes()
    assert Pipeline(**args).run()['status']=='completed'
    assert (tmp_path/'out/v3_final_ranking.json').read_bytes()==original
