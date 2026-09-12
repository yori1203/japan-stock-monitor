import io
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from edinet_adapter import EdinetAdapter, EdinetConfig, EdinetFinancialData, parse_xbrl
from financials import FinancialData, _statement_metadata
from financial_crosscheck import CrosscheckConfig, financial_crosscheck
from v3_warning_audit import audit


def pair(value=500):
    em={"period_start":"2025-04-01","period_end":"2026-03-31",
        "scope":"consolidated","original_unit":"JPY","tag":"{test}NetSales",
        "selection_ambiguous":False}
    ym={"period_start":"2025-04-01","period_end":"2026-03-31",
        "scope":"consolidated","original_unit":"JPY"}
    return (FinancialData('1',revenue=1000,field_metadata={'revenue':ym}),
            EdinetFinancialData('1',revenue=value,field_metadata={'revenue':em}))


def check(y,e):
    return financial_crosscheck(y,e,CrosscheckConfig(require_provenance=True))


class ProvenanceTests(unittest.TestCase):
    def test_numeric_match_requires_verified_scope_period_and_unit(self):
        for key in ('scope','period_end','original_unit'):
            with self.subTest(missing=key):
                y,e=pair(1000)
                y.field_metadata['revenue'].pop(key)
                f=check(y,e).fields[0]
                self.assertEqual(f.numeric_status,'matched')
                self.assertEqual(f.status,'not_comparable')
                self.assertFalse(f.diagnostics['eligible'])
        y,e=pair(1000)
        self.assertEqual(check(y,e).fields[0].status,'matched')

    def test_legacy_crosscheck_stays_unchanged(self):
        y=FinancialData('1',revenue=1000);e=EdinetFinancialData('1',revenue=1000)
        self.assertEqual(financial_crosscheck(y,e).fields[0].status,'matched')

    def test_target_tag_inventory_includes_unmapped_issuer_fact(self):
        raw=b'''<xbrl xmlns:j="urn:j"><context id="CurrentYearDuration"><period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context><unit id="JPY"><measure>iso4217:JPY</measure></unit><j:NetSales contextRef="CurrentYearDuration" unitRef="JPY">5</j:NetSales><j:IssuerSpecificSales contextRef="CurrentYearDuration" unitRef="JPY">100</j:IssuerSpecificSales></xbrl>'''
        e=parse_xbrl(raw,'7203','E001','DOC')
        inv=e.field_metadata['revenue']['statement_tag_audit']
        self.assertEqual(inv['doc_id'],'DOC')
        self.assertIn('IssuerSpecificSales',[f['tag_local'] for f in inv['facts']])
        self.assertEqual(e.revenue,5)

    def test_statement_total_selection_never_uses_closest_value(self):
        from financial_crosscheck import select_comparison_fact
        ns='{http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor}'
        base={'tag':ns+'NetSales','tag_local':'NetSales','period_start':'2025-04-01',
              'period_end':'2026-03-31','scope':'unknown','original_unit':'JPY'}
        total={**base,'dimensions':[],'context_id':'CurrentYearDuration','normalized_value':500}
        segment={**base,'dimensions':['x:SegmentMember'],'context_id':'Segment','normalized_value':1000}
        ym={'period_end':'2026-03-31','period_kind':'annual','source_field':'Total Revenue'}
        for cs in ([segment,total],[total,segment]):
            value,meta=select_comparison_fact('revenue',ym,{**segment,'candidates':cs},1000)
            self.assertEqual(value,500)
            self.assertFalse(meta['selection_ambiguous'])
            self.assertEqual(meta['scope'],'consolidated')

    def test_ifrs_parent_profit_is_extracted_and_selected(self):
        from financial_crosscheck import select_comparison_fact
        raw=b'''<xbrl xmlns:j="http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2025-11-01/jpigp_cor"><context id="CurrentYearDuration"><period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context><unit id="JPY"><measure>iso4217:JPY</measure></unit><j:ProfitLossAttributableToOwnersOfParentIFRS contextRef="CurrentYearDuration" unitRef="JPY">7000</j:ProfitLossAttributableToOwnersOfParentIFRS></xbrl>'''
        e=parse_xbrl(raw,'1')
        value,meta=select_comparison_fact('net_income',{'source_field':'Net Income'},e.field_metadata['net_income'],e.net_income)
        self.assertEqual(value,7000)
        self.assertEqual(meta['tag_local'],'ProfitLossAttributableToOwnersOfParentIFRS')

    def test_same_period_conflicting_totals_remain_incomparable(self):
        y,e=pair(500)
        c={**e.field_metadata['revenue'],'tag_local':'NetSales','context_id':'CurrentYearDuration','dimensions':[],'normalized_value':500}
        e.field_metadata['revenue']['candidates']=[c,{**c,'normalized_value':1000}]
        self.assertEqual(check(y,e).fields[0].status,'not_comparable')

    def test_known_prior_annual_fact_can_replace_interim_but_not_double_it(self):
        from financial_crosscheck import select_comparison_fact
        c={'tag':'{test}NetSales','tag_local':'NetSales','scope':'consolidated','dimensions':[],
           'context_id':'InterimDuration','period_start':'2026-01-01','period_end':'2026-06-30','normalized_value':50}
        annual={**c,'context_id':'PriorYearDuration','period_start':'2025-01-01','period_end':'2025-12-31','normalized_value':90}
        ym={'period_kind':'annual','period_end':'2025-12-31'}
        value,m=select_comparison_fact('revenue',ym,{**c,'all_period_candidates':[c,annual]},50)
        self.assertEqual(value,90)
        value,m=select_comparison_fact('revenue',ym,{**c,'candidates':[c]},50)
        self.assertEqual(value,50)

    def test_undated_trailing_eps_is_not_a_proven_difference(self):
        y,e=pair()
        y=replace(y,eps=100,field_metadata={'eps':{'period_kind':'trailing','source':'info','source_field':'trailingEps'}})
        e=replace(e,eps=50)
        f=next(f for f in check(y,e).fields if f.field=='eps')
        self.assertEqual(f.status,'not_comparable')
        self.assertIn('undated_trailing_period',f.diagnostics['causes'])

    def test_provider_evidence_absence_is_explicit_not_true_difference(self):
        y,e=pair(500)
        y.field_metadata['revenue'].update(source='annual_statement',scope='unknown')
        c={**e.field_metadata['revenue'],'tag_local':'NetSales','context_id':'CurrentYearDuration','dimensions':[],'normalized_value':500}
        e.field_metadata['revenue']['candidates']=[c]
        f=check(y,e).fields[0]
        self.assertEqual(f.status,'not_comparable')
        self.assertIn('comparison_basis_unavailable',f.diagnostics['causes'])
        self.assertFalse(f.diagnostics['eligible'])

    def test_alphanumeric_security_does_not_overwrite_numeric_issuer(self):
        import zipfile
        from edinet_adapter import normalize_stock_code, parse_edinet_code_list
        self.assertEqual(normalize_stock_code('３６６Ａ０'),'366A')
        self.assertEqual(normalize_stock_code('36600'),'3660')
        data='ＥＤＩＮＥＴコード,提出者名,提出者業種,証券コード\nE26301,istyle,IT,36600\nE37743,Wellness,IT,366A0\n'
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('codes.csv',data.encode('cp932'))
        mapping=parse_edinet_code_list(b.getvalue())
        self.assertEqual(mapping['3660'].edinet_code,'E26301')
        self.assertEqual(mapping['366A'].edinet_code,'E37743')

    def test_legacy_code_map_cache_is_refreshed(self):
        from tests.test_edinet_adapter import code_zip
        from datetime import datetime,timezone
        class Transport:
            calls=0
            def get(self,url,timeout):self.calls+=1;return code_zip()
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'code-list.json').write_text(json.dumps({'fetched_at':datetime.now(timezone.utc).isoformat(),
                'entries':{'1234':{'edinet_code':'WRONG','stock_code':'1234','company_name':'wrong'}}}))
            t=Transport();a=EdinetAdapter('fixture',transport=t,cache_dir=d)
            self.assertEqual(a.fetch_code_map()['1234'].edinet_code,'E00001')
            self.assertEqual(t.calls,1)

    def test_aligned_match_and_true_difference(self):
        y,e=pair(1000)
        self.assertEqual(check(y,e).fields[0].status,'matched')
        f=check(y,replace(e,revenue=500)).fields[0]
        self.assertEqual(f.status,'warning')
        self.assertIn('substantive_difference',f.diagnostics['causes'])

    def test_half_year_is_not_annual_difference_even_when_end_matches(self):
        y,e=pair()
        y.field_metadata['revenue'].update(period_start=None,period_kind='annual')
        e.field_metadata['revenue']['period_start']='2025-10-01'
        r=check(y,e)
        self.assertEqual(r.fields[0].status,'not_comparable')
        self.assertTrue(r.period_mismatch)
        self.assertNotIn('revenue_mismatch',r.warnings)

    def test_balance_dates_are_field_specific(self):
        y,e=pair()
        y=replace(y,total_assets=1000,period_end='2026-03-31')
        e=replace(e,total_assets=900,period_end='2026-03-31')
        y.field_metadata['total_assets']={**y.field_metadata['revenue'],'period_start':None}
        e.field_metadata['total_assets']={**e.field_metadata['revenue'],'period_start':None,'period_end':'2025-12-31'}
        f=next(f for f in check(y,e).fields if f.field=='total_assets')
        self.assertEqual(f.status,'not_comparable')

    def test_scope_and_tag_conflicts_are_not_numeric_warnings(self):
        for key,value,reason in [('scope','non_consolidated','scope_mismatch'),('selection_ambiguous',True,'xbrl_tag_selection')]:
            with self.subTest(key=key):
                y,e=pair();e.field_metadata['revenue'][key]=value
                f=check(y,e).fields[0]
                self.assertEqual(f.status,'not_comparable')
                self.assertIn(reason,f.diagnostics['causes'])

    def test_known_yen_cannot_be_scaled_to_force_match(self):
        y,e=pair(1)
        f=check(y,e).fields[0]
        self.assertEqual(f.unit_multiplier,1)
        self.assertEqual(f.status,'warning')

    def test_different_currencies_are_not_comparable(self):
        y,e=pair();y.field_metadata['revenue']['original_unit']='USD'
        self.assertEqual(check(y,e).fields[0].status,'not_comparable')

    def test_missing_metadata_does_not_claim_true_difference(self):
        y=FinancialData('1',revenue=1000);e=EdinetFinancialData('1',revenue=500)
        f=check(y,e).fields[0]
        self.assertEqual(f.status,'not_comparable')
        self.assertFalse(f.diagnostics['eligible'])
        self.assertEqual(check(y,replace(e,revenue=1000)).fields[0].status,'not_comparable')

    def test_heuristic_unit_match_is_not_verified(self):
        f=check(FinancialData('1',revenue=1000),EdinetFinancialData('1',revenue=1)).fields[0]
        self.assertEqual(f.unit_multiplier,1000)
        self.assertFalse(f.diagnostics['eligible'])
        self.assertIn('unit_correction',f.diagnostics['causes'])

    def test_missing_data_is_separate(self):
        y,e=pair(None)
        f=check(y,e).fields[0]
        self.assertEqual(f.status,'unavailable')
        self.assertIn('data_missing',f.diagnostics['causes'])

    def test_xbrl_provenance_preserves_selection_and_scale(self):
        raw=b'''<xbrl xmlns:j="urn:j"><context id="CurrentNonConsolidatedDuration"><period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context><context id="CurrentConsolidatedDuration"><period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context><unit id="JPY"><measure>iso4217:JPY</measure></unit><j:NetSales contextRef="CurrentNonConsolidatedDuration" unitRef="JPY" scale="3" decimals="-3">5</j:NetSales><j:NetSales contextRef="CurrentConsolidatedDuration" unitRef="JPY">7000</j:NetSales></xbrl>'''
        e=parse_xbrl(raw,'1');m=e.field_metadata['revenue']
        self.assertEqual(e.revenue,5000)
        self.assertEqual(m['scope'],'non_consolidated')
        self.assertEqual(m['normalization_multiplier'],1000)
        self.assertEqual(m['original_unit'],'JPY')
        self.assertTrue(m['selection_ambiguous'])
        self.assertEqual(m['candidate_count'],2)

    def test_no_filing_evidence_requires_completed_window(self):
        from tests.test_edinet_adapter import code_zip
        class Transport:
            def get(self,url,timeout):
                if 'Edinetcode' in url:return code_zip()
                return b'{"results":[]}'
        with tempfile.TemporaryDirectory() as d:
            a=EdinetAdapter('fixture',transport=Transport(),cache_dir=d,
                config=EdinetConfig(rate_limit_delay=0),sleeper=lambda _:None)
            self.assertEqual(a.find_latest_documents(['1234'],end_date=date(2026,9,11),lookback_days=3),{})
            info=a.document_search_diagnostics['1234']
            self.assertEqual(info['scanned_days'],3)
            self.assertEqual(info['supported_xbrl_filings'],0)
            self.assertEqual(info['window_start'],'2026-09-09')

    def test_legacy_warning_not_invented_as_resolved(self):
        text='''# Report
- データ期間不一致数: 5
- 単位補正数: 4
## 1234 Company
- 対象年度/期間: 2025-04-01 - 2026-03-31
- EDINET/Yahoo状態: ok / ok
| revenue | 500 | 1000 | warning |
| equity | 100 | 100 | matched |
'''
        r=audit(text)
        self.assertEqual(r['original_warning_causes']['undetermined'],1)
        self.assertEqual(r['after']['review'],1)
        self.assertEqual(r['after']['matched'],1)
        self.assertEqual(r['resolved_warnings'],0)
        self.assertIsNone(r['fields'][0]['unit_multiplier'])

    def test_cached_snapshot_recalculates_instead_of_trusting_saved_status(self):
        y,e=pair(500)
        items=[]
        for name in ('revenue','operating_income','net_income','total_assets','equity','eps'):
            items.append({'name':name,'edinet':getattr(e,name),'yahoo':getattr(y,name),
                'status':'matched','numeric_status':'warning' if name=='revenue' else 'unavailable',
                'diagnostics':{'edinet':e.field_metadata.get(name,{}),'yahoo':y.field_metadata.get(name,{})}})
        text='```json\n'+json.dumps({'schema':'v3-crosscheck-v1','rows':[
            {'code':'1','edinet_status':'ok','fields':items}]})+'\n```'
        r=audit(text)
        self.assertEqual(r['after']['warning'],1)
        self.assertEqual(r['original_warning_causes']['substantive_difference'],1)


if __name__=='__main__': unittest.main()
