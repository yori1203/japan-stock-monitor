"""Japanese report for provider acquisition coverage, separate from mock reports."""
from collections import Counter
from pathlib import Path
from tdnet_event_report import safe
from tdnet_events import JST, timestamp


def build_provider_report(result, *, ranking_changes=()):
    counts = Counter(e.event_type.value for e in result.events)
    lines = ["# V3 TDnet実データProvider検証", "",
        f"- 取得処理日時 JST: {timestamp(result.fetched_at).astimezone(JST).isoformat()}",
        f"- 対象日: {', '.join(result.target_dates)}", f"- status: {result.status}",
        f"- source: {safe(result.source)} / is_mock: {result.is_mock}",
        f"- 対象銘柄: {', '.join(result.codes) or '対象日の全銘柄'}",
        f"- 取得イベント数: {len(result.events)}", f"- 成功ページ数: {result.success_count}",
        f"- 失敗リクエスト・解析数: {result.failure_count}",
        f"- 実通信リクエスト数: {result.request_count}", f"- cache hit（ページ数）: {result.cache_hits}",
        f"- warning: {' / '.join(safe(w) for w in result.warnings) or 'なし'}", "",
        "未取得・部分取得は0点にせず、TDnet未照合として配点を再正規化します。", "",
        "## イベント分類", "", "| イベント型 | 件数 |", "|---|---:|"]
    lines.extend(f"| {kind} | {count} |" for kind, count in sorted(counts.items()))
    if not counts: lines.append("| 取得なし | 0 |")
    lines += ["", "## 重要イベント", "", "| 銘柄 | 開示日時 | イベント型 | impact | source |", "|---|---|---|---:|---|"]
    # Report identifiers/derived scores; do not republish the raw disclosure corpus.
    lines += [f"| {safe(e.code)} | {e.published_at.isoformat()} | {e.event_type.value} | {e.impact_score} | {safe(e.source)} |"
              for e in sorted(result.events, key=lambda e: -abs(e.impact_score-50))[:10]]
    lines += ["", "## ランキング接続", "", *ranking_changes, "",
        "## 利用条件", "",
        "公開されていることを自動収集・複製の許可とは扱いません。今回、利用許諾を確認できないため通常設定の実取得は無効です。",
        "- [JPX利用条件](https://www.jpx.co.jp/term-of-use/index.html)",
        "- [TDnet掲載ページの免責事項](https://www.release.tdnet.info/inbs/js/I_MENSEKI.js)", ""]
    return "\n".join(lines)


def write_provider_report(result, path="v3_tdnet_events_report.md", **kwargs):
    path = Path(path)
    path.write_text(build_provider_report(result, **kwargs), encoding="utf-8")
    return path
