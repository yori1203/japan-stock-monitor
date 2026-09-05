import unittest
from datetime import datetime,timezone
from edinet_adapter import EdinetFinancialData
from financial_crosscheck import apply_edinet_crosscheck, edinet_risk_flags, financial_crosscheck
from financials import FinancialData, score_financial_candidate
from preselection import MarketSnapshot,score_preselection
from universe import UniverseSecurity

NOW=datetime(2026,9,5,tzinfo=timezone.utc)
def yahoo(**kw):
    d=dict(code='1',revenue=1000,operating_income=100,net_income=50,eps=10,fetched_at=NOW.isoformat());d.update(kw);return FinancialData(**d)
def edinet(**kw):
    d=dict(code='1',revenue=1000,operating_income=100,net_income=50,total_assets=2000,equity=1000,eps=10,shares_outstanding=110,previous_shares_outstanding=100);d.update(kw);return EdinetFinancialData(**d)
def candidate():
    s=UniverseSecurity('1','Co','Prime','IT',100,'test',NOW);m=MarketSnapshot('1',100,10000,1e6,.1,.2,90,80,-.1,0,.02,252,'2026-09-04','test')
    return score_financial_candidate(score_preselection(s,m),yahoo(),now=NOW)
class CrosscheckTests(unittest.TestCase):
    def test_match(self):self.assertEqual(financial_crosscheck(yahoo(),edinet()).crosscheck_score,100)
    def test_mismatch(self):
        r=financial_crosscheck(yahoo(),edinet(revenue=500));self.assertIn('revenue_mismatch',r.warnings)
    def test_unit_difference(self):
        r=financial_crosscheck(yahoo(revenue=1_000_000),edinet(revenue=1000));self.assertEqual(r.fields[0].status,'matched');self.assertEqual(r.fields[0].unit_multiplier,1000)
    def test_year_gap_not_automatically_warning(self):
        r=financial_crosscheck(yahoo(),edinet(period_end='2024-03-31'));self.assertEqual(r.fields[0].status,'matched')
    def test_risk_flags(self):
        e=edinet(equity=-1,operating_income=-1,shares_outstanding=130,previous_equity=500,previous_total_assets=1000)
        flags=edinet_risk_flags(e);self.assertIn('edinet_negative_equity',flags);self.assertIn('edinet_shares_outstanding_increase',flags)
    def test_absent_edinet_keeps_score(self):
        c=candidate();self.assertEqual(apply_edinet_crosscheck(c,None),c)
    def test_confirmation_improves_quality(self):
        c=candidate();updated=apply_edinet_crosscheck(c,edinet());self.assertGreaterEqual(updated.financial_data_quality_score,c.financial_data_quality_score)
