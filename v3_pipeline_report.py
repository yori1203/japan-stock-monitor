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
    lines += ["", "## data freshness", "", "| データ | 最古の取得日時・基準日 | 最新 | 状態 | 理由 |", "|---|---|---|---|---|"]
    for name, f in fresh.items(): lines.append(f"| {name} | {f['oldest'] or '未取得'} | {f['newest'] or '未取得'} | {f.get('status', 'unknown')} | {f.get('reason', f['warning'])} |")
    ed = run.get("edinet_counts", {})
    lines += ["", f"- EDINET: {ed}（no_recent_filingはAPI失敗ではありません）",
              f"- EDINET検索基準日: {run.get('edinet_reference_date', '未取得')}",
              f"- TDnet: {run.get('tdnet_status', 'unavailable')}（未取得は0点にせず再正規化）",
              *[f"- warning: {safe(w)}" for w in run.get("warnings", [])]]
    for heading, subset in [("総合Top20", ranked[:20]), ("5万円以下Top10", [c for c in ranked if c["minimum_purchase_amount"] is not None and c["minimum_purchase_amount"] <= 50000][:10]),
                            ("1万円前後の候補（5千〜1万5千円）", [c for c in ranked if c["minimum_purchase_amount"] is not None and 5000 <= c["minimum_purchase_amount"] <= 15000]),
                            ("A評価銘柄", [c for c in ranked if c["category"] == "A"])]:
        lines += ["", f"## {heading}", "", *table(subset)]
    lines += ["", "## 保有株評価（専用portfolio universe）", "", "候補Top50への採否とは独立した分析です。スコアは取得できた項目で再正規化し、売買は自動実行しません。", ""]
    for p in portfolio:
        action_label = {"hold": "保有継続", "buy_more": "買い増し候補", "take_profit_watch": "利確警戒", "stop_loss_watch": "損切り警戒", "reduce": "縮小検討", "insufficient_data": "分析データ不足"}
        lines += [f"### {p['code']} {safe(p['company_name'])}（{p['shares']}株）", "", "| 項目 | 値 |", "|---|---|"]
        fields = [("取得単価 average_cost", p['average_cost'] if p['average_cost'] is not None else "未登録"),
            ("現在株価（日足）", p['current_price']), ("株価基準日", p['price_date']), ("前日比 %", p['change_pct']), ("出来高", p['volume']),
            ("含み損益", p['unrealized_pnl'] if p['unrealized_pnl'] is not None else "取得単価未登録のため算出不可"),
            ("含み損益 %", p['unrealized_pnl_pct'] if p['unrealized_pnl_pct'] is not None else "取得単価未登録のため算出不可"),
            ("portfolio_score", p['portfolio_score']), ("ランキング相当スコア", p['ranking_equivalent_score']),
            ("暫定action", f"{p['action']}（{action_label[p['action']]}）"), ("confidence", p['confidence']),
            ("financial status", p['financial_status']), ("technical status", p['technical_status']),
            ("EDINET status", p['edinet_status']), ("TDnet status", p['tdnet_status']),
            ("positive_flags", ', '.join(p['positive_flags']) or 'なし'), ("risk_flags", ', '.join(p['risk_flags']) or 'なし'),
            ("評価可能な配点", f"{p['available_weight']} / 100（未取得項目は除外）")]
        fields += [("スコア内訳", ', '.join(f"{k}={v:.2f}" for k, v in p['components'].items() if v is not None)),
                   ("取得できた評価項目", ', '.join(p['available_components']) or 'なし'),
                   ("取得できなかった評価項目", ', '.join(p['missing_components']) or 'なし')]
        for label, value in fields: lines.append(f"| {label} | {safe(str(value)) if value is not None else '未取得'} |")
        tech = p['technical']
        lines += [f"- MA5/25/75: {tech.get('ma5', '未取得')} / {tech.get('ma25', '未取得')} / {tech.get('ma75', '未取得')}",
                  f"- RSI: {tech.get('rsi', '未取得')} / MACD: {tech.get('macd', '未取得')}"]
        for kind, f in p['freshness'].items(): lines.append(f"- {kind}: {f['status']} / {f['oldest'] or '未取得'} / {f['reason']}")
        lines += [f"- 評価理由: {safe('; '.join(p['evaluation_reasons'])) or '評価項目不足'}", f"- コメント: {p['comment']}", ""]
    lines += ["", "## 前回ランキングとの差", ""]
    for key, label in (("up", "順位上昇"), ("down", "順位下落"), ("new", "新規候補"), ("top20_new", "新規Top20"), ("removed", "対象外となった候補")):
        lines.append(f"- {label}: {', '.join(changes[key]) or 'なし'}")
    for flagtype in ("positive_flags", "risk_flags"):
        counts = Counter(f for c in ranked for f in c.get(flagtype, []))
        lines += ["", f"## 重要{flagtype}", "", *(f"- {safe(f)}: {n}銘柄" for f, n in counts.most_common())]
        if not counts: lines.append("- なし（TDnet未照合では材料の不存在を意味しません）")
    return "\n".join(lines) + "\n"
