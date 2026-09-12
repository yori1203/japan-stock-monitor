"""Offline audit of a validation Markdown artifact; never reads credentials.

Legacy Markdown has rounded values and no field provenance. Those omissions
remain unknown. A legacy warning is never relabelled as a proven correction.
"""
from __future__ import annotations
import argparse
import json
import re
from dataclasses import asdict
from collections import Counter
from pathlib import Path
from financials import FinancialData
from edinet_adapter import EdinetFinancialData
from financial_crosscheck import CrosscheckConfig, comparison_diagnostics, financial_crosscheck

CATEGORIES = ("period_mismatch", "scope_mismatch", "unit_correction",
              "xbrl_tag_selection", "substantive_difference", "data_missing",
              "semantic_mismatch", "undated_trailing_period", "comparison_basis_unavailable", "undetermined")
LABELS = dict(zip(CATEGORIES, ("対象期間不一致", "連結/単体不一致", "単位補正",
                             "XBRLタグ選択", "実質的な数値差異", "データ欠損", "項目定義不一致", "TTM対象期間未開示", "比較根拠が取得応答にない", "根拠不足・未確定")))


def number(s):
    try: return float(s.replace(",", ""))
    except ValueError: return None


def read_rows(text):
    for block in re.findall(r"```json\s*(.*?)\s*```", text, re.S):
        payload = json.loads(block)
        if payload.get("schema") == "v3-crosscheck-v1":
            rows=payload["rows"]
            for row in rows:
                values={f['name']:f for f in row['fields']}
                names=('revenue','operating_income','net_income','total_assets','equity','eps')
                y=FinancialData(code=row['code'],**{n:values.get(n,{}).get('yahoo') for n in names},
                    field_metadata={n:values.get(n,{}).get('diagnostics',{}).get('yahoo',{}) for n in names})
                e=EdinetFinancialData(code=row['code'],**{n:values.get(n,{}).get('edinet') for n in names},
                    field_metadata={n:values.get(n,{}).get('diagnostics',{}).get('edinet',{}) for n in names})
                checked=financial_crosscheck(y,e,CrosscheckConfig(require_provenance=True))
                for f in checked.fields:
                    target=values[f.field]
                    target['before_status']=target.get('numeric_status') or target['status']
                    target['edinet']=f.edinet_value
                    target.update({k:v for k,v in asdict(f).items() if k in
                        ('difference_ratio','unit_multiplier','numeric_status','diagnostics','status')})
            return rows, False
    rows=[]
    for code, body in re.findall(r"^## (\d{4}) (.*?)(?=^## |\Z)",text,re.M|re.S):
        period=re.search(r"対象年度/期間: (\d{4}-\d\d-\d\d) - (\d{4}-\d\d-\d\d)",body)
        status=re.search(r"EDINET/Yahoo状態: ([^\n]+)",body)
        row={"code":code,"company_name":body.splitlines()[0],"fields":[],
             "edinet_status":status.group(1).split(" / ")[0] if status else "unknown"}
        for name,ev,yv,state in re.findall(r"^\| (\w+) \| ([^|]+) \| ([^|]+) \| (matched|warning|unavailable) \|",body,re.M):
            ev,yv=number(ev.strip()),number(yv.strip())
            # A company-wide date is evidence about the report, not necessarily
            # the selected fact's context. Do not pretend it is field metadata.
            e=EdinetFinancialData(code=code)
            y=FinancialData(code=code)
            diag=comparison_diagnostics(name,y,e,1,missing=ev is None or yv is None)
            new_state="review" if state=="warning" else state
            row["fields"].append({"name":name,"edinet":ev,"yahoo":yv,"before_status":state,
                "status":new_state,"numeric_status":state,
                "difference_ratio":abs(yv-ev)/max(abs(yv),abs(ev),1) if ev is not None and yv is not None else None,
                "ratio_basis":"rounded artifact values; unadjusted; original ratio unavailable",
                "unit_multiplier":None,"diagnostics":diag,
                "document_period":list(period.groups()) if period else None})
        rows.append(row)
    if not rows: raise ValueError("No supported validation rows found")
    return rows, True


def audit(text):
    rows, legacy=read_rows(text)
    counts=Counter(); before=Counter(); after=Counter(); warning_causes=Counter()
    detailed=[]
    for row in rows:
        for f in row["fields"]:
            old=f.get("before_status",f.get("numeric_status",f["status"]))
            before[old]+=1; after[f["status"]]+=1
            d=f.get("diagnostics",{}); causes=d.get("causes",[])
            category=next((c for c in CATEGORIES if c in causes),"undetermined")
            if f["status"]=="warning" and d.get("eligible"): category="substantive_difference"
            if old=="warning": warning_causes[category]+=1
            counts[category]+=1
            detailed.append({"code":row["code"],**f,"category":category})
    def original_count(label):
        m=re.search(rf"^- {re.escape(label)}: (\d+)",text,re.M)
        return int(m.group(1)) if m else None
    return {"legacy_rounded_artifact":legacy,"before":dict(before),"after":dict(after),
        "original_warning_causes":{c:warning_causes[c] for c in CATEGORIES},
        "all_item_causes":{c:counts[c] for c in CATEGORIES},
        "original_period_mismatches":original_count("データ期間不一致数"),
        "original_unit_corrections":original_count("単位補正数"),
        "resolved_warnings":0 if legacy else None,
        "fields":detailed,"symbols":[{"code":r["code"],"status":r["edinet_status"]} for r in rows]}


def write_audit(source, output):
    result=audit(Path(source).read_text(encoding="utf-8"))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    (output/"warning_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# EDINET warning監査", "",
        "元artifactにない情報はunknown。reviewへの分類は警告の解消や正しい数値の確認ではありません。",
        "旧artifactの差異率は丸め済み表示値の補正前概算で、元の計算結果ではありません。", "",
        f"- 修正前: {result['before']}",f"- 修正後: {result['after']}",
        f"- 旧期間不一致数: {result['original_period_mismatches']}",
        f"- 旧単位補正数: {result['original_unit_corrections']}","",
        "| 元warningの分類 | 件数 |","|---|---:|"]
    lines += [f"| {LABELS[c]} | {n} |" for c,n in result["original_warning_causes"].items()]
    lines += ["","| コード | 項目 | EDINET | Yahoo | 差異率（概算） | EDINET文書期間 | Yahoo期間 | 連結 E/Y | タグ | 元単位 E/Y | 適用倍率 | 前→後 | 理由 |",
              "|---|---|---:|---:|---:|---|---|---|---|---|---|---|---|"]
    for f in result["fields"]:
        d=f.get("diagnostics",{});e=d.get("edinet",{});y=d.get("yahoo",{})
        values=[f['code'],f['name'],f['edinet'],f['yahoo'],
            f"{f['difference_ratio']:.2%}" if f.get('difference_ratio') is not None else 'unknown',
            f.get('document_period') or [e.get('period_start'),e.get('period_end')],
            [y.get('period_start'),y.get('period_end')],
            f"{e.get('scope','unknown')}/{y.get('scope','unknown')}",e.get('tag','unknown'),
            f"{e.get('original_unit','unknown')}/{y.get('original_unit','unknown')}",f.get('unit_multiplier'),
            f"{f.get('before_status',f.get('numeric_status'))} → {f['status']}",d.get('reason','unknown')]
        lines.append('| '+' | '.join(str(v if v is not None else 'unknown').replace('|','\\|') for v in values)+' |')
    (output/'warning_audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return {k:v for k,v in result.items() if k not in ('fields','symbols')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cached-report',required=True)
    parser.add_argument('--output-dir',default='validation/warning-audit')
    args=parser.parse_args()
    print(json.dumps(write_audit(args.cached_report,args.output_dir),ensure_ascii=True))
