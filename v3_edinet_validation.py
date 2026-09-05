"""Manual GitHub Actions runner for EDINET/Yahoo real-data validation."""
from __future__ import annotations
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

from edinet_adapter import EdinetAdapter, EdinetConfig, EdinetResult
from financial_crosscheck import financial_crosscheck
from financials import YahooFinanceAdapter

TARGET_CODES = ("8614", "2317", "4477", "3679", "2492", "8628", "3660", "7803", "7354", "8789", "7203", "8306", "6758", "9432", "9984")
SMOKE_CODES = TARGET_CODES[:5]
FIELDS = ("revenue", "operating_income", "net_income", "total_assets", "equity", "eps", "shares_outstanding")
JST = timezone(timedelta(hours=9))

def fmt(value):
    if value is None: return "未取得"
    if isinstance(value, float): return f"{value:,.4g}"
    return str(value)

def build_report(rows, started, *, smoke_rows=(), diagnostics=None):
    edinet_ok=sum(r["edinet_status"]=="ok" for r in rows); yahoo_ok=sum(r["yahoo_status"]=="ok" for r in rows)
    matched=sum(r["matched"] for r in rows); warnings=sum(r["warnings"] for r in rows)
    periods=sum(r["period_mismatch"] for r in rows); units=sum(r["unit_corrections"] for r in rows)
    lines=["# V3 EDINET Validation Report", "", f"- 実行日時 JST: {started.astimezone(JST).isoformat(timespec='seconds')}",
           f"- 対象銘柄数: {len(rows)}", f"- EDINET取得成功数: {edinet_ok}", f"- 取得失敗数: {len(rows)-edinet_ok}",
           f"- Yahoo照合成功数: {yahoo_ok}", f"- matched数: {matched}", f"- warning数: {warnings}",
           f"- データ期間不一致数: {periods}", f"- 単位補正数: {units}", "",
           "APIキーはレポートおよびログへ出力していません。", ""]
    if smoke_rows:
        lines += ["## 5銘柄スモーク検証", "",
                  f"- 対象銘柄数: {len(smoke_rows)}",
                  f"- EDINET取得成功数: {sum(r['edinet_status']=='ok' for r in smoke_rows)}",
                  f"- 取得失敗数: {sum(r['edinet_status']!='ok' for r in smoke_rows)}", ""]
    if diagnostics:
        lines += ["## 安全な診断情報", "",
                  f"- コードリストHTTP status: {diagnostics.http_status if diagnostics.http_status is not None else 'cache'}",
                  f"- Content-Type: {diagnostics.content_type or '未取得'}",
                  f"- CSVファイル: {diagnostics.archive_member or 'cache'}",
                  f"- 文字コード: {diagnostics.encoding or 'cache'}",
                  f"- ヘッダー行: {diagnostics.header_row or 'cache'}",
                  f"- 証券コード対応件数: {diagnostics.mapped_rows}",
                  f"- 診断: {diagnostics.reason or '正常'}", ""]
    for r in rows:
        lines += [f"## {r['code']} {r['company_name']}", "", f"- EDINETコード: {r['edinet_code']}",
          f"- 最新対象書類: {r['document_name']}", f"- 対象年度/期間: {r['period']}",
          f"- EDINET/Yahoo状態: {r['edinet_status']} / {r['yahoo_status']}",
          f"- EDINET失敗理由: {r['failure_reason'] or 'なし'}",
          f"- crosscheck_score: {fmt(r['score'])}", f"- risk_flags: {', '.join(r['risks']) or 'なし'}",
          f"- 取得できなかった項目: {', '.join(r['missing']) or 'なし'}", "",
          "| 項目 | EDINET | Yahoo | 判定 |", "|---|---:|---:|---|"]
        for item in r["fields"]: lines.append(f"| {item['name']} | {fmt(item['edinet'])} | {fmt(item['yahoo'])} | {item['status']} |")
        lines.append("")
    return "\n".join(lines)

def _validate(codes, adapter, code_map, yahoo):
    docs=adapter.find_latest_documents(codes,lookback_days=190); rows=[]
    for code in codes:
        entry=code_map.get(code); document=docs.get(code)
        if not entry:
            er=EdinetResult("unavailable", reason=f"EDINET code not found (normalized={code}, map_entries={len(code_map)})")
        elif not document:
            er=EdinetResult("unavailable", reason=f"useful XBRL document not found within 190 days (edinet_code={entry.edinet_code})")
        else:
            er=adapter.fetch_document(code,document)
        try: yd=yahoo.fetch(code); ys="ok"
        except Exception as exc: yd=None; ys=f"error ({type(exc).__name__})"
        check=financial_crosscheck(yd,er.data) if yd and er.data else None
        data=er.data; comparisons={f.field:f for f in check.fields} if check else {}
        fields=[]
        for name in FIELDS:
            item=comparisons.get(name); fields.append({"name":name,"edinet":getattr(data,name,None),"yahoo":getattr(yd,name,None),"status":item.status if item else "unavailable"})
        rows.append({"code":code,"company_name":entry.company_name if entry else "不明","edinet_code":entry.edinet_code if entry else "未取得",
          "document_name":data.document_name if data else "未取得","period":f"{data.period_start or '?'} - {data.period_end or '?'}" if data else "未取得",
          "edinet_status":er.status,"failure_reason":er.reason,"yahoo_status":ys,"score":check.crosscheck_score if check else None,
          "risks":check.edinet_risk_flags+check.warnings if check else (),
          "missing":[f"EDINET:{n}" for n in FIELDS if getattr(data,n,None) is None] + [f"Yahoo:{n}" for n in FIELDS if n != "shares_outstanding" and getattr(yd,n,None) is None],
          "matched":sum(f.status=="matched" for f in check.fields) if check else 0,"warnings":sum(f.status=="warning" for f in check.fields) if check else 0,
          "period_mismatch":bool(check and check.period_mismatch),"unit_corrections":sum(f.unit_multiplier!=1 for f in check.fields) if check else 0,"fields":fields})
    return rows


def run(report_path="v3_edinet_validation_report.md"):
    if not os.getenv("EDINET_API_KEY"): raise RuntimeError("EDINET_API_KEY repository secret is not configured")
    started=datetime.now(timezone.utc); cache=Path(".cache/v3-edinet-validation")
    adapter=EdinetAdapter(config=EdinetConfig(timeout=30,max_retries=2,retry_delay=2,rate_limit_delay=.2,cache_ttl_hours=24),cache_dir=cache/"edinet")
    code_map=adapter.fetch_code_map(); yahoo=YahooFinanceAdapter()
    missing=[code for code in TARGET_CODES if code not in code_map]
    diag=adapter.code_map_diagnostics
    print(f"EDINET code map: entries={len(code_map)} http_status={diag.http_status or 'cache'} missing_targets={len(missing)} reason={diag.reason or 'ok'}")
    smoke_rows=_validate(SMOKE_CODES,adapter,code_map,yahoo)
    smoke_ok=sum(r["edinet_status"]=="ok" for r in smoke_rows)
    print(f"Smoke validation: targets={len(smoke_rows)} edinet_ok={smoke_ok}")
    rows=_validate(TARGET_CODES,adapter,code_map,yahoo)
    Path(report_path).write_text(build_report(rows,started,smoke_rows=smoke_rows,diagnostics=diag),encoding="utf-8")
    edinet_ok=sum(r['edinet_status']=='ok' for r in rows)
    print(f"Validation completed: targets={len(rows)} edinet_ok={edinet_ok} yahoo_ok={sum(r['yahoo_status']=='ok' for r in rows)}")
    if smoke_ok < 1 or edinet_ok < 1:
        raise RuntimeError("EDINET validation failed: no symbol completed code mapping, document selection, XBRL download and extraction")
    return rows

if __name__ == "__main__": run()
