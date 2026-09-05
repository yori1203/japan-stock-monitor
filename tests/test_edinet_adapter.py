import io, json, tempfile, unittest, zipfile
from datetime import date
from pathlib import Path
from edinet_adapter import EdinetAdapter, EdinetConfig, parse_edinet_code_list, parse_xbrl

def code_zip():
    data='ＥＤＩＮＥＴコード,提出者名,提出者業種,証券コード\nE00001,テスト株式会社,情報通信,12340\n'.encode('cp932')
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('EdinetcodeDlInfo.csv',data)
    return out.getvalue()

def xbrl(ifrs=False, revenue=1000, shares=120):
    tag='RevenueIFRS' if ifrs else 'NetSales'
    return f'''<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:jppfs="urn:jppfs" xmlns:ifrs="urn:ifrs"><context id="Current"><period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context><context id="Prior"><period><startDate>2024-04-01</startDate><endDate>2025-03-31</endDate></period></context><jppfs:{tag} contextRef="Current" unitRef="JPY">{revenue}</jppfs:{tag}><jppfs:OperatingIncome contextRef="Current">100</jppfs:OperatingIncome><jppfs:Assets contextRef="Current">2000</jppfs:Assets><jppfs:NetAssets contextRef="Current">800</jppfs:NetAssets><jppfs:BasicEarningsLossPerShare contextRef="Current">10</jppfs:BasicEarningsLossPerShare><jppfs:NumberOfIssuedSharesTotalNumberOfShares contextRef="Current">{shares}</jppfs:NumberOfIssuedSharesTotalNumberOfShares><jppfs:NumberOfIssuedSharesTotalNumberOfShares contextRef="Prior">100</jppfs:NumberOfIssuedSharesTotalNumberOfShares>{'<ifrs:marker>0</ifrs:marker>' if ifrs else ''}</xbrl>'''.encode()

class Transport:
    def __init__(self,fail=0):self.calls=0;self.fail=fail
    def get(self,url,timeout):
        self.calls+=1
        if self.calls<=self.fail:raise OSError('offline')
        if 'Edinetcode' in url:return code_zip()
        if 'documents.json' in url:return json.dumps({'results':[{'edinetCode':'E00001','docTypeCode':'120','xbrlFlag':'1','docID':'D1','submitDateTime':'2026-06-01'}]}).encode()
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('XBRL/PublicDoc/test.xbrl',xbrl())
        return out.getvalue()

class EdinetTests(unittest.TestCase):
    def test_no_api_key(self):
        with tempfile.TemporaryDirectory() as d:self.assertEqual(EdinetAdapter('',cache_dir=d).fetch('1234').status,'unavailable')
    def test_code_conversion(self):
        self.assertEqual(parse_edinet_code_list(code_zip())['1234'].edinet_code,'E00001')
    def test_jgaap_normalization(self):
        data=parse_xbrl(xbrl(), '1234');self.assertEqual(data.accounting_standard,'J-GAAP');self.assertEqual(data.revenue,1000)
    def test_ifrs_normalization(self):
        data=parse_xbrl(xbrl(True), '1234');self.assertEqual(data.accounting_standard,'IFRS');self.assertEqual(data.revenue,1000)
    def test_shares_growth(self):
        self.assertAlmostEqual(parse_xbrl(xbrl(shares=120),'1234').shares_outstanding_growth,.2)
    def test_retry_and_cache(self):
        t=Transport(fail=1)
        with tempfile.TemporaryDirectory() as d:
            a=EdinetAdapter('key',transport=t,cache_dir=d,config=EdinetConfig(max_retries=2,retry_delay=0,rate_limit_delay=0),sleeper=lambda _:None)
            first=a.fetch('1234',target_date=date(2026,6,1));calls=t.calls
            second=a.fetch('1234',target_date=date(2026,6,1))
        self.assertEqual(first.status,'ok');self.assertTrue(second.cache_hit);self.assertEqual(t.calls,calls)
    def test_failure_is_safe(self):
        with tempfile.TemporaryDirectory() as d:
            result=EdinetAdapter('key',transport=Transport(fail=99),cache_dir=d,config=EdinetConfig(max_retries=1,retry_delay=0),sleeper=lambda _:None).fetch('1234')
        self.assertEqual(result.status,'error')
