"""Markdown reporting for V3 final candidates."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from final_ranking import FinalCandidate, FinalRankingResult

JST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class FinalReportStats:
    universe_count: int
    preselection_count: int
    financial_success_count: int
    edinet_success_count: int
    missing_data_count: int = 0
    yahoo_edinet_mismatch_count: int = 0


def _amount(value: float | None) -> str:
    return "未取得" if value is None else f"{value:,.0f}円"


def _candidate_lines(item: FinalCandidate) -> list[str]:
    reasons = "、".join(item.score_reasons[:4]) or "明確な加点理由なし"
    warnings = "、".join(item.warning_reasons[:4]) or "特記事項なし"
    return [
        f"### {item.rank}. {item.code} {item.company_name}", "",
        f"- final_score: {item.final_score:.2f}（カテゴリ{item.category}）",
        f"- 市場 / 業種: {item.market} / {item.industry or '未取得'}",
        f"- 最低購入金額: {_amount(item.minimum_purchase_amount)}",
        f"- 主な加点理由: {reasons}",
        f"- 主な注意点: {warnings}", "",
    ]


def build_final_report(result: FinalRankingResult, stats: FinalReportStats,
                       *, generated_at: datetime | None = None) -> str:
    generated = generated_at or datetime.now(timezone.utc)
    categories = Counter(item.category for item in result.ranked_candidates)
    flags = Counter(flag for item in result.ranked_candidates for flag in item.risk_flags)
    lines = [
        "# V3 Final Candidates Report", "",
        f"- 実行日時 JST: {generated.astimezone(JST).isoformat(timespec='seconds')}",
        f"- 対象ユニバース数: {stats.universe_count}",
        f"- preselection対象数: {stats.preselection_count}",
        f"- 財務取得成功数: {stats.financial_success_count}",
        f"- EDINET取得成功数: {stats.edinet_success_count}",
        f"- 最終候補数: {len(result.ranked_candidates)}",
        f"- カテゴリ数: A={categories['A']} / B={categories['B']} / C={categories['C']} / D={categories['D']}",
        f"- データ欠損件数: {stats.missing_data_count}",
        f"- Yahoo/EDINET不一致件数: {stats.yahoo_edinet_mismatch_count}",
        f"- 主なrisk_flags: {', '.join(f'{name}={count}' for name, count in flags.most_common(10)) or 'なし'}", "",
        "## Top 20", "",
    ]
    for item in result.top_20:
        lines.extend(_candidate_lines(item))
    lines += ["## Top 10", ""]
    for item in result.top_10:
        lines.extend(_candidate_lines(item))
    lines += ["## 5万円以下 Top 10", ""]
    for item in result.small_investment_top_10:
        lines.extend(_candidate_lines(item))
    return "\n".join(lines)


def write_final_report(result: FinalRankingResult, stats: FinalReportStats,
                       path: str | Path = "v3_final_candidates_report.md",
                       *, generated_at: datetime | None = None) -> Path:
    target = Path(path)
    target.write_text(build_final_report(result, stats, generated_at=generated_at), encoding="utf-8")
    return target
