"""Japanese integrated V3 reporting and non-executing portfolio review."""
from collections import Counter
from datetime import timedelta
from tdnet_events import JST, timestamp
from tdnet_event_report import safe


def freshness(values, now, max_days):
    dates = []
    for value in values:
        if value:
            try: dates.append(timestamp(value))
            except (ValueError, TypeError): pass
    if not dates:
        return {"oldest": None, "newest": None, "warning": "取得日時・基準日不明"}
    age = (timestamp(now) - min(dates)).total_seconds() / 86400
    return {"oldest": min(dates).isoformat(), "newest": max(dates).isoformat(),
            "warning": "古いデータを含む" if age > max_days else "未来日時を含む" if max(dates) > timestamp(now) else "なし"}


def ranking_changes(current, previous):
    old = {str(c["code"]): c for c in previous}
    result = {"up": [], "down": [], "new": [], "top20_new": [], "removed": []}
    for c in current:
        code = c["code"]
        if code not in old: result["new"].append(code)
        elif c["rank"] < old[code]["rank"]: result["up"].append(code)
        elif c["rank"] > old[code]["rank"]: result["down"].append(code)
        if c["rank"] <= 20 and (code not in old or old[code]["rank"] > 20): result["top20_new"].append(code)
    result["removed"] = sorted(set(old) - {c["code"] for c in current})
    return result


def portfolio_review(portfolio, ranking, snapshots):
    indexed = {c["code"]: c for c in ranking}
    result = []
    for holding in portfolio:
        code = str(holding["code"])
        c, quote = indexed.get(code), snapshots.get(code, {})
        price = quote.get("current_price")
        cost = holding.get("average_cost", holding.get("purchase_price"))
        pnl = (price / cost - 1) if price and cost and cost > 0 else None
        risks = c.get("risk_flags", []) if c else []
        if c is None:
            verdict = "評価保留"
        elif set(risks) & {"negative_equity", "edinet_negative_equity", "delisting_risk", "major_dilution", "ms_warrant"} or (pnl is not None and pnl <= -.15):
            verdict = "損切り警戒"
        elif pnl is not None and pnl >= .30 and (c["final_score"] < 70 or risks):
            verdict = "利確警戒"
        elif c["final_score"] >= 80 and not risks and c.get("positive_flags"):
            verdict = "買い増し候補"
        else:
            verdict = "保有継続"
        result.append({"code": code, "shares": holding.get("shares"), "score": c["final_score"] if c else None,
                       "price": price, "price_date": quote.get("data_as_of"), "unrealized_rate": pnl,
                       "assessment": verdict, "risk_flags": risks, "positive_flags": c.get("positive_flags", []) if c else [],
                       "missing": [name for name, missing in (("財務・総合スコア", c is None), ("取得単価", cost is None), ("株価", price is None)) if missing]})
    return result


def table(items):
    rows = ["| 順位 | コード | 会社名 | score | 評価 | 最低購入金額 |", "|---:|---|---|---:|---|---:|"]
    rows += [f"| {c['rank']} | {c['code']} | {safe(c['company_name'])} | {c['final_score']:.2f} | {c['category']} | {format(c['minimum_purchase_amount'], ',.0f') + '円' if c['minimum_purchase_amount'] is not None else '未取得'} |" for c in items]
    if not items: rows.append("| — | — | 該当なし | — | — | — |")
    return rows


def build_report(run, ranked, portfolio, fresh, changes):
    lines = ["# V3統合パイプライン", "", f"- 実行日時 JST: {timestamp(run['started_at']).astimezone(JST).isoformat()}",
             f"- mode: {run['mode']} / status: {run['status']}", f"- 実行時間: {run.get('elapsed_seconds', 0):.2f}秒",
             "- 保存データの日時は下表参照。自動売買なし。", "", "## stage状態", "",
             "| stage | status | 入力 | 成功 | 失敗 | cache hit |", "|---|---|---:|---:|---:|---:|"]
    for name, s in run["stages"].items():
        lines.append(f"| {name} | {s['status']} | {s['input_count']} | {s['success_count']} | {s['failure_count']} | {s['cache_hits']} |")
    lines += ["", "## data freshness", "", "| データ | 最古の取得日時・基準日 | 最新 | warning |", "|---|---|---|---|"]
    for name, f in fresh.items(): lines.append(f"| {name} | {f['oldest'] or '未取得'} | {f['newest'] or '未取得'} | {f['warning']} |")
    ed = run.get("edinet_counts", {})
    lines += ["", f"- EDINET: {ed}（no_recent_filingはAPI失敗ではありません）",
              f"- EDINET検索基準日: {run.get('edinet_reference_date', '未取得')}",
              f"- TDnet: {run.get('tdnet_status', 'unavailable')}（未取得は0点にせず再正規化）",
              *[f"- warning: {safe(w)}" for w in run.get("warnings", [])]]
    for heading, subset in [("総合Top20", ranked[:20]), ("5万円以下Top10", [c for c in ranked if c["minimum_purchase_amount"] is not None and c["minimum_purchase_amount"] <= 50000][:10]),
                            ("1万円前後の候補（5千〜1万5千円）", [c for c in ranked if c["minimum_purchase_amount"] is not None and 5000 <= c["minimum_purchase_amount"] <= 15000]),
                            ("A評価銘柄", [c for c in ranked if c["category"] == "A"])]:
        lines += ["", f"## {heading}", "", *table(subset)]
    lines += ["", "## 保有株評価", "", "売買指示ではなく、確認優先度の目安です。取得単価不明の場合、損益による利確・損切り判定はできません。", "",
              "| 銘柄 | 株数 | 評価 | score | 株価 | 株価基準日 | 不足情報 |", "|---|---:|---|---:|---:|---|---|"]
    for p in portfolio:
        lines.append(f"| {p['code']} | {p['shares']} | {p['assessment']} | {p['score'] if p['score'] is not None else '未取得'} | {p['price']} | {p['price_date']} | {', '.join(p['missing']) or 'なし'} |")
        if p['risk_flags'] or p['positive_flags']: lines.append(f"\n{p['code']}: risk={p['risk_flags']}, positive={p['positive_flags']}\n")
    lines += ["", "## 前回ランキングとの差", ""]
    for key, label in (("up", "順位上昇"), ("down", "順位下落"), ("new", "新規候補"), ("top20_new", "新規Top20"), ("removed", "対象外となった候補")):
        lines.append(f"- {label}: {', '.join(changes[key]) or 'なし'}")
    for flagtype in ("positive_flags", "risk_flags"):
        counts = Counter(f for c in ranked for f in c.get(flagtype, []))
        lines += ["", f"## 重要{flagtype}", "", *(f"- {safe(f)}: {n}銘柄" for f, n in counts.most_common())]
        if not counts: lines.append("- なし（TDnet未照合では材料の不存在を意味しません）")
    return "\n".join(lines) + "\n"
