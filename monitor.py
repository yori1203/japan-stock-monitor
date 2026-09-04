from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from history import append_unique, migrate_history
from market_data import download_stock
from strategy import add_indicators, evaluate_rows

JST = ZoneInfo("Asia/Tokyo")


def load_config(path: str = "config.json") -> dict:
    with open(path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    for section in ("portfolio", "watchlist"):
        if not isinstance(config.get(section, []), list):
            raise ValueError(f"{section} は配列で指定してください")
    return config


def report_session(now: datetime, explicit: str | None = None) -> str:
    if explicit in {"morning", "evening"}:
        return explicit
    return "morning" if now.astimezone(JST).hour < 12 else "evening"


def analyse(code: str, category: str, priority: str) -> dict:
    market = download_stock(code)
    frame = add_indicators(market.frame).dropna()
    if len(frame) < 2:
        raise ValueError("分析に必要なデータが不足しています")
    latest, previous = frame.iloc[-1], frame.iloc[-2]
    decision = evaluate_rows(latest, previous)
    return {
        "code": code, "category": category, "priority": priority,
        "price": round(float(latest["Close"]), 1), "rsi": round(float(latest["RSI"]), 1),
        "ma5": round(float(latest["MA5"]), 1), "ma25": round(float(latest["MA25"]), 1),
        "ma75": round(float(latest["MA75"]), 1), "macd": round(float(latest["MACD"]), 2),
        "macd_signal": round(float(latest["MACD_SIGNAL"]), 2),
        "data_as_of": market.data_as_of, "fetched_at": market.fetched_at,
        "source": market.source, **decision.to_dict(),
    }


def configured_stocks(config: dict) -> list[dict]:
    stocks = []
    for category in ("portfolio", "watchlist"):
        for item in config.get(category, []):
            value = item if isinstance(item, dict) else {"code": str(item)}
            stocks.append({"code": str(value["code"]), "category": category,
                           "priority": value.get("priority", "normal"), "shares": value.get("shares")})
    return stocks


def discover(config: dict, excluded: set[str]) -> tuple[list[dict], list[dict]]:
    settings = config.get("auto_discovery", {})
    if not settings.get("enabled", False):
        return [], []
    candidates, errors = [], []
    for raw_code in settings.get("candidate_codes", []):
        code = str(raw_code)
        if code in excluded:
            continue
        try:
            candidates.append(analyse(code, "discovery", "normal"))
        except Exception as exc:
            errors.append({"code": code, "category": "discovery", "error": str(exc)})
    candidates.sort(key=lambda item: (item["score"], item["volume_ratio"]), reverse=True)
    return candidates[: int(settings.get("top_candidates", 5))], errors


def allowed(result: dict, config: dict) -> bool:
    enabled = config.get("signals", {})
    key = result["signal_key"]
    return enabled.get(key, True) if key in enabled else True


def create_report(results: list[dict], discoveries: list[dict], errors: list[dict], *,
                  generated_at: datetime, session: str, output: Path) -> None:
    session_ja = "朝" if session == "morning" else "夕"
    lines = [f"# 📊 日本株 自動監視レポート（{session_ja}）", "",
             f"- レポート生成日時（JST）：{generated_at:%Y-%m-%d %H:%M:%S}",
             "- データ種別：日足（リアルタイム価格ではありません）",
             "- 取得元：Yahoo Finance（yfinance）", "",
             "> 表示価格は各銘柄の「データ基準日」時点の日足終値です。取得日時と市場データの基準日は異なる場合があります。", ""]
    sections = [("portfolio", "保有銘柄"), ("watchlist", "監視銘柄"), ("discovery", "自動探索候補")]
    all_results = results + discoveries
    for category, title in sections:
        items = [item for item in all_results if item["category"] == category]
        lines.extend([f"## {title}", ""])
        if not items:
            lines.extend(["対象なし", ""])
        for result in items:
            lines.extend([f"### {result['code']}", "", f"**{result['signal']}**", "",
                          f"- データ基準日：{result['data_as_of']}",
                          f"- データ取得日時（JST）：{result['fetched_at']:%Y-%m-%d %H:%M:%S}",
                          f"- 日足終値：{result['price']}円", f"- 総合スコア：**{result['score']} / 100**",
                          f"- 確信度：**{result['confidence']}**", f"- RSI：{result['rsi']}",
                          f"- 移動平均（5/25/75日）：{result['ma5']} / {result['ma25']} / {result['ma75']}円",
                          f"- 出来高倍率：{result['volume_ratio']}倍", "", "**判定理由**"])
            lines.extend(f"- {reason}" for reason in result["reasons"])
            lines.extend(["", "---", ""])
    if errors:
        lines.extend(["## 取得エラー", ""])
        lines.extend(f"- {item['code']}（{item['category']}）：{item['error']}" for item in errors)
        lines.append("")
    lines.extend(["## 注意事項", "",
                  "- 本レポートは日足データによる機械的な監視結果で、リアルタイム情報や投資助言ではありません。",
                  "- 売買前に適時開示、出来高、注文板、取引コスト等を別途確認してください。"])
    output.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--signals", default="signals.csv")
    parser.add_argument("--session", choices=["morning", "evening"])
    args = parser.parse_args(argv)
    config = load_config(args.config)
    report_settings = config.get("daily_report", {})
    if not report_settings.get("enabled", True):
        print("daily_report.enabled=false のため監視をスキップしました")
        return 0
    now = datetime.now(JST)
    session = report_session(now, args.session or os.getenv("REPORT_SESSION"))
    output = Path(report_settings.get(f"{session}_file", f"report_{session}.md"))
    run_id = f"{now:%Y%m%d}-{session}"
    migrate_history(args.signals)
    results, errors = [], []
    stocks = configured_stocks(config)
    for stock in stocks:
        try:
            result = analyse(stock["code"], stock["category"], stock["priority"])
            result["shares"] = stock["shares"]
            if allowed(result, config):
                results.append(result)
        except Exception as exc:
            errors.append({**stock, "error": str(exc)})
    discoveries, discovery_errors = discover(config, {stock["code"] for stock in stocks})
    errors.extend(discovery_errors)
    create_report(results, discoveries, errors, generated_at=now, session=session, output=output)
    latest_output = Path(report_settings.get("latest_file", "report.md"))
    if latest_output != output:
        latest_output.write_text(output.read_text(encoding="utf-8"), encoding="utf-8")
    rows = [{"date": now.strftime("%Y-%m-%d %H:%M"), "code": r["code"], "price": r["price"],
             "score": r["score"], "signal": r["signal"], "confidence": r["confidence"], "rsi": r["rsi"],
             "session": session, "data_as_of": r["data_as_of"], "category": r["category"],
             "priority": r["priority"], "signal_key": r["signal_key"], "source": r["source"], "run_id": run_id}
            for r in results + discoveries]
    append_unique(args.signals, rows)
    print(f"{session} report: {len(results)} configured, {len(discoveries)} discovered, {len(errors)} errors")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
