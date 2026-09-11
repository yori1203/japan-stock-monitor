"""Separate V3 holdings analysis; bounded acquisition of holdings only.

python v3_portfolio.py --refresh updates missing/stale holdings data only.
The integrated cached/quick pipeline never calls acquisition.
"""
from dataclasses import asdict
from pathlib import Path
import argparse
import json
import math
import subprocess
import sys
import time
from tdnet_events import timestamp
from v3_freshness import age_freshness, price_freshness
from v3_edinet_full_validation import atomic_json

WEIGHTS = dict(financial=25, technical=25, momentum=15, edinet=15, risk=10, event=10)
NAMES = {"4597": "ソレイジア・ファーマ", "4596": "窪田製薬HD", "6573": "CRAVIA", "6740": "ジャパンディスプレイ"}


def load(path, default=None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default


def holdings(root):
    config = load(Path(root) / "config.json", {}).get("portfolio", [])
    overrides = load(Path(root) / "v3_portfolio_settings.json", {}).get("holdings", {})
    # Optional null defaults must not erase an already registered V2 cost.
    return [{**h, **{k: v for k, v in overrides.get(str(h["code"]), {}).items() if v is not None}, "code": str(h["code"])} for h in config]


def weighted_score(components, weights=None):
    weights = WEIGHTS if weights is None else weights
    available = {k: max(0., min(100., float(v))) for k, v in components.items() if v is not None and math.isfinite(float(v))}
    total = sum(max(0, weights.get(k, 0)) for k in available)
    return round(sum(v * max(0, weights.get(k, 0)) for k, v in available.items()) / total, 2) if total else None


def action_for(score, risks, technical, pnl=None):
    if set(risks) & {"negative_equity", "edinet_negative_equity", "delisting_risk", "major_dilution", "ms_warrant"} or (pnl is not None and pnl <= -.15):
        return "stop_loss_watch"
    if score is None: return "insufficient_data"
    if score < 40: return "reduce"
    if (technical.get("rsi") or 0) >= 75 or (pnl is not None and pnl >= .30 and score < 70): return "take_profit_watch"
    if score >= 75 and not risks: return "buy_more"
    return "hold"


def decision_reasons(action, score, risks, technical, price_status):
    """Explain the chosen assessment and the alternatives without trade orders."""
    return {
        "保有継続": "強い売却・買い増し条件に該当せず、継続監視" if action == "hold" else "別の警戒・評価条件を優先",
        "買い増し候補": "75点以上かつ観測リスクなし" if action == "buy_more" else "75点以上・観測リスクなしの条件未成立、または警戒条件を優先",
        "利確警戒": "RSI過熱、または含み益と評価低下を確認" if action == "take_profit_watch" else "RSI過熱・含み益条件未成立、または重大リスクを優先（取得単価なしでは損益条件未評価）",
        "損切り警戒": "債務超過等の重大リスク、または登録取得単価に対し15%以上の含み損" if action == "stop_loss_watch" else "重大リスク・含み損条件未成立（取得単価なしでは損益条件未評価）",
        "様子見": "株価が古い/基準日不明のため最新データ確認待ち。暫定評価と併記" if price_status in {"stale", "unknown"} else "分析データ不足のため判断保留" if action == "insufficient_data" else "鮮度条件による保留なし。暫定評価を参照",
        "縮小検討": f"総合{score:.2f}点が40点未満" if action == "reduce" else "40点未満ではない、または重大リスクを優先",
    }


def analyse(h, raw, snapshot, now):
    from financials import FinancialData, score_financial_candidate, CORE_QUALITY_FIELDS
    from preselection import MarketSnapshot, score_preselection
    from universe import UniverseSecurity
    from final_ranking import rank_financial_candidates, RISK_PENALTIES
    from financial_crosscheck import financial_crosscheck
    from edinet_adapter import EdinetFinancialData
    from tdnet_adapter import TDnetResult
    from tdnet_events import TDnetEvent
    from tdnet_event_scoring import summarize_events
    code = h["code"]
    # No hypothetical input may contribute to a production portfolio either.
    raw = {} if raw.get("is_mock") else raw
    snapshot = {} if snapshot.get("is_mock") else snapshot
    tech = raw.get("technical", {})
    if tech.get("is_mock"): tech = {}
    price = tech.get("current_price", snapshot.get("current_price"))
    price_date = tech.get("data_as_of", snapshot.get("data_as_of"))
    components = {k: None for k in WEIGHTS}
    risks, positives, reasons = [], [], []
    fin = raw.get("financial", {})
    if fin.get("is_mock") or fin.get("code", code) != code: fin = {}
    fin = {k: v for k, v in fin.items() if k in FinancialData.__dataclass_fields__}
    ed = raw.get("edinet", {})
    if ed.get("is_mock") or ed.get("data", {}).get("code", code) != code: ed = {}
    td = raw.get("tdnet", {})
    allowed_td = td.get("status") in {"ok", "partial"} and not td.get("is_mock")
    events = tuple(TDnetEvent(**e) for e in td.get("events", []) if not e.get("is_mock") and e.get("code") == code) if allowed_td else ()
    td_status = td.get("status", "unavailable") if allowed_td and (events or not td.get("events")) else "unavailable"
    event_summary = summarize_events(events, status="ok" if td_status in {"ok", "partial"} else "unavailable", as_of=now)
    components["event"] = event_summary.tdnet_event_score
    positives.extend(event_summary.positive_flags); risks.extend(event_summary.risk_flags)
    equivalent = None
    financial_status, ed_status = "unavailable", ed.get("status", "unavailable")
    if any(fin.get(k) is not None for k in CORE_QUALITY_FIELDS):
        security = UniverseSecurity(code=code, company_name=h.get("company_name", NAMES.get(code, code)), market="", industry="", trading_unit=100, source="portfolio", fetched_at=now)
        snap = dict(code=code, current_price=price or 0, average_volume=0, average_trading_value=0, price_change_20d=None,
                    price_change_60d=None, ma20=None, ma60=None, distance_from_52w_high=None, zero_volume_ratio=0,
                    daily_volatility=None, observation_count=0, data_as_of=price_date or "")
        snap.update({k: v for k, v in snapshot.items() if k in MarketSnapshot.__dataclass_fields__})
        snap.update(code=code, current_price=price or 0)
        c = score_financial_candidate(score_preselection(security, MarketSnapshot(**snap)), FinancialData(**fin), now=timestamp(now))
        checks = {}
        if ed_status == "ok" and ed.get("data"):
            checks[code] = financial_crosscheck(c.financial_data, EdinetFinancialData(**ed["data"]))
        ranked = rank_financial_candidates([c], crosschecks=checks, edinet_statuses={code: ed_status},
            tdnet_results={code: TDnetResult(td_status, events)}, generated_at=str(now)).ranked_candidates[0]
        components.update(financial=c.financial_score, edinet=ranked.crosscheck_score)
        risks.extend(ranked.risk_flags); positives.extend(ranked.positive_flags)
        positives.extend(r for r in c.score_reasons if r in {"strong_revenue_growth", "strong_operating_margin", "operating_income_improving", "strong_equity_ratio", "positive_fcf"})
        equivalent = ranked.final_score
        financial_status = "ok" if c.financial_data_quality_score >= 60 else "partial"
        reasons.extend(c.score_reasons)
        components["risk"] = max(0, 100 - sum(RISK_PENALTIES.get(r, 20) for r in set(risks)))
    if tech.get("score") is not None:
        reasons.extend(tech.get("reasons", []))
        components["technical"] = tech["score"]
        if tech["score"] < 40: risks.append("weak_technical")
        if tech["score"] >= 70: positives.append("strong_technical")
    momentum = tech.get("momentum_20d", snapshot.get("price_change_20d"))
    if momentum is not None:
        components["momentum"] = max(0, min(100, 50 + 200 * momentum))
        if momentum < -.1: risks.append("negative_momentum")
        if momentum > .1: positives.append("positive_momentum")
    cost = h.get("average_cost", h.get("purchase_price"))
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost <= 0: cost = None
    pnl = price / cost - 1 if price is not None and cost else None
    score = weighted_score(components)
    fresh = {"stock_price": price_freshness([price_date], now), "fundamentals": age_freshness([fin.get("fetched_at")], now, 7),
             "fundamentals_period": age_freshness([fin.get("period_end")], now, 180),
             "EDINET": age_freshness([ed.get("data", {}).get("fetched_at") or ed.get("checked_at")], now, 7),
             "TDnet": age_freshness([td.get("fetched_at") if td_status in {"ok", "partial"} else None], now, 7)}
    coverage = sum(WEIGHTS[k] for k, v in components.items() if v is not None)
    confidence = "medium" if coverage >= 60 else "low"
    if fresh["stock_price"]["status"] in {"stale", "unknown"}: confidence = "low"
    action = action_for(score, risks, tech, pnl)
    return dict(code=code, company_name=h.get("company_name", NAMES.get(code, code)), shares=h.get("shares"), average_cost=cost,
        purchase_price_status="registered" if cost else "missing", cost_basis_status="registered" if cost else "missing",
        current_price=price, price_date=price_date, previous_close=tech.get("previous_close"), change_pct=tech.get("change_pct"),
        volume=tech.get("volume"), technical=tech, financial_data=fin, edinet_data=ed,
        unrealized_pnl=(price-cost)*h.get("shares", 0) if pnl is not None else None, unrealized_pnl_pct=round(pnl*100, 2) if pnl is not None else None,
        portfolio_score=score, components=components, available_weight=coverage, ranking_equivalent_score=equivalent,
        available_components=[k for k, v in components.items() if v is not None],
        missing_components=[k for k, v in components.items() if v is None],
        evaluation_reasons=reasons + ([f"20営業日騰落率 {momentum:.2%}"] if momentum is not None else []),
        action=action, confidence=confidence,
        decision_reasons=decision_reasons(action, score, risks, tech, fresh["stock_price"]["status"]),
        financial_status=financial_status, technical_status="ok" if tech.get("score") is not None else "partial" if momentum is not None else "unavailable",
        edinet_status=ed_status, tdnet_status=td_status, positive_flags=sorted(set(positives)), risk_flags=sorted(set(risks)), freshness=fresh,
        comment="市場・財務・材料ベースの暫定判定。" + ("取得単価未登録のため損益率・厳密な利確/損切り価格は算出不可。" if cost is None else "損益は別レイヤーで判定。") + ("一部データ未照合。" if coverage < 100 else ""))


def analyse_portfolio(root, cache, snapshots, financials, edinet, now):
    saved = load(Path(root) / "validation/v3_portfolio_input.json", {})
    saved.update(load(Path(cache) / "portfolio-cache.json", {}))
    result = []
    for h in holdings(root):
        code = h["code"]
        raw = dict(saved.get(code, {}))
        if code in financials and not raw.get("financial"): raw["financial"] = financials[code]
        if code in edinet and not raw.get("edinet"): raw["edinet"] = edinet[code]
        result.append(analyse(h, raw, snapshots.get(code, raw.get("snapshot", {})), now))
    return result


def acquire(kind, code):
    if kind == "financial":
        from financials import YahooFinanceAdapter
        return asdict(YahooFinanceAdapter().fetch(code))
    if kind == "technical":
        from market_data import download_stock
        from strategy import evaluate_frame
        market = download_stock(code, attempts=1)
        frame, decisions = evaluate_frame(market.frame)
        row = frame.iloc[-1]
        value = lambda x: float(x) if math.isfinite(float(x)) else None
        previous = value(frame.iloc[-2]["Close"]) if len(frame) > 1 else None
        decision = decisions[-1]
        return dict(current_price=value(row.Close), previous_close=previous, change_pct=(float(row.Close)/previous-1)*100 if previous else None,
            volume=value(row.Volume), ma5=value(row.MA5), ma25=value(row.MA25), ma75=value(row.MA75), rsi=value(row.RSI),
            macd=value(row.MACD), macd_signal=value(row.MACD_SIGNAL), score=decision.score if decision else None,
            momentum_20d=float(row.Close)/float(frame.iloc[-21].Close)-1 if len(frame)>20 else None,
            data_as_of=market.data_as_of, fetched_at=market.fetched_at.isoformat(), source=market.source,
            reasons=list(decision.reasons) if decision else [], is_mock=False)
    raise ValueError("unsupported acquisition")


def refresh(root, timeout=45):
    root = Path(root)
    cache = root / ".cache/v3-pipeline"
    cache.mkdir(parents=True, exist_ok=True)
    saved = load(root / "validation/v3_portfolio_input.json", {})
    saved.update(load(cache / "portfolio-cache.json", {}))
    checkpoints = load(cache / "portfolio-refresh.json", {})
    now = timestamp()
    for h in holdings(root):
        code = h["code"]
        raw = saved.setdefault(code, {})
        for kind in ("technical", "financial"):
            value = raw.get(kind, {})
            valid = price_freshness([value.get("data_as_of")], now) if kind == "technical" else age_freshness([value.get("fetched_at")], now, 7)
            if valid["status"] in {"fresh", "acceptable"}: continue
            key = code + ":" + kind
            last = checkpoints.get(key, {})
            if last.get("status") == "error" and (now-timestamp(last["at"])).total_seconds() < 3600: continue
            out = cache / "portfolio-worker.json"
            out.unlink(missing_ok=True)
            try:
                result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", kind, "--code", code, "--output", str(out)],
                    timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode or not out.exists(): raise RuntimeError("provider unavailable")
                data = load(out)
                if kind == "technical" and (data.get("score") is None or not data.get("data_as_of")):
                    raise RuntimeError("incomplete technical response")
                if kind == "financial" and (data.get("code") != code or not any(data.get(k) is not None for k in ("revenue", "net_income", "equity", "eps"))):
                    raise RuntimeError("incomplete financial response")
                raw[kind] = data
                checkpoints[key] = dict(status="ok", at=now.isoformat())
            except (subprocess.TimeoutExpired, RuntimeError) as exc:
                checkpoints[key] = dict(status="error", at=now.isoformat(), reason=type(exc).__name__)
            atomic_json(cache / "portfolio-cache.json", saved)
            atomic_json(cache / "portfolio-refresh.json", checkpoints)
            print(key, checkpoints[key]["status"], flush=True)
            time.sleep(2)
    return checkpoints


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--worker")
    parser.add_argument("--code")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.worker: atomic_json(args.output, acquire(args.worker, args.code))
    elif args.refresh: refresh(Path(__file__).resolve().parent)
