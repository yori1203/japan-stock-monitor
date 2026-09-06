"""Auditable offline TDnet event report (mock is prominently labeled)."""
from collections import Counter
from pathlib import Path
from tdnet_events import JST, timestamp
from tdnet_event_scoring import summarize_events


def safe(value):
    return str(value).replace("|", "／").replace("\n", " ").replace("\r", " ").replace("<", "＜").replace(">", "＞")


def build_tdnet_event_report(results, *, generated_at=None):
    now = timestamp(generated_at)
    summaries = {code: summarize_events(r.events, status=r.status, as_of=now) for code, r in sorted(results.items())}
    events = [e for s in summaries.values() for e in s.events]
    counts = Counter(e.event_type.value for e in events)
    dilution = sum(counts[k] for k in ("share_issuance", "warrant", "stock_option"))
    primary = sum(counts[k] for k in ("upward_revision", "downward_revision", "dividend_increase", "share_buyback")) + dilution
    lines = ["# V3 TDnet Event Report", "",
        "**MOCK：仮想イベントによる検証です。実際のIR取得結果ではありません。**" if any(r.status == "mock" for r in results.values()) else "TDnet取得状態は銘柄別表を参照。未契約・取得エラーはイベントなしと区別します。", "",
        f"- 実行日時 JST: {now.astimezone(JST).isoformat()}",
        f"- 対象銘柄数: {len(results)}", f"- event取得数: {len(events)}",
        f"- 上方修正件数: {counts['upward_revision']}", f"- 下方修正件数: {counts['downward_revision']}",
        f"- 希薄化件数: {dilution}", f"- 増配件数: {counts['dividend_increase']}",
        f"- 自社株買い件数: {counts['share_buyback']}", f"- その他重要IR件数: {len(events) - primary}", ""]
    for label, selected in (("positive top events", sorted((e for e in events if e.impact_score > 50), key=lambda e: -e.impact_score)),
                            ("negative top events", sorted((e for e in events if e.impact_score < 50), key=lambda e: e.impact_score))):
        lines += [f"## {label}", "", "| code | published_at | type | impact | confidence | title |", "|---|---|---|---:|---:|---|"]
        lines += [f"| {safe(e.code)} | {e.published_at.isoformat()} | {e.event_type.value} | {e.impact_score} | {e.confidence} | {safe(e.title)} |" for e in selected[:10]]
        lines.append("")
    lines += ["## 銘柄別tdnet_event_score", "", "| code | provider | status | fetched_at | score | adjustment | positive_flags | risk_flags |",
              "|---|---|---|---|---:|---:|---|---|"]
    for code, s in summaries.items():
        r = results[code]
        lines.append(f"| {safe(code)} | {safe(r.provider_name)} | {safe(s.status)} | {safe(r.fetched_at)} | {s.tdnet_event_score if s.tdnet_event_score is not None else 'TDnet未照合'} | {s.adjustment:+.2f} | {', '.join(s.positive_flags)} | {', '.join(s.risk_flags)} |")
    return "\n".join(lines) + "\n"


def write_tdnet_event_report(results, path="v3_tdnet_event_report.md", *, generated_at=None):
    path = Path(path)
    path.write_text(build_tdnet_event_report(results, generated_at=generated_at), encoding="utf-8")
    return path
