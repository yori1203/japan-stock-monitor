from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from market_data import download_stock
from strategy import evaluate_frame


@dataclass(frozen=True)
class Trade:
    signal_date: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    return_pct: float


def load_codes(config_path: str) -> list[str]:
    with open(config_path, "r", encoding="utf-8") as stream:
        config = json.load(stream)
    items = config.get("portfolio", []) + config.get("watchlist", [])
    return list(dict.fromkeys(str(item["code"] if isinstance(item, dict) else item) for item in items))


def simulate(frame: pd.DataFrame, hold_days: int = 10) -> list[Trade]:
    enriched, decisions = evaluate_frame(frame)
    trades: list[Trade] = []
    position = 0
    while position < len(enriched) - hold_days - 1:
        decision = decisions[position]
        if decision is None or decision.signal_key not in {"buy", "add"}:
            position += 1
            continue
        entry_pos, exit_pos = position + 1, position + 1 + hold_days
        entry, exit_price = float(enriched["Open"].iloc[entry_pos]), float(enriched["Close"].iloc[exit_pos])
        if entry > 0:
            trades.append(Trade(pd.Timestamp(enriched.index[position]).strftime("%Y-%m-%d"),
                                pd.Timestamp(enriched.index[entry_pos]).strftime("%Y-%m-%d"),
                                pd.Timestamp(enriched.index[exit_pos]).strftime("%Y-%m-%d"),
                                entry, exit_price, (exit_price - entry) / entry * 100))
            position = exit_pos + 1
        else:
            position += 1
    return trades


def summarize(code: str, trades: list[Trade]) -> dict:
    returns = [trade.return_pct for trade in trades]
    if not returns:
        return {"code": code, "trades": 0, "win_rate": 0.0, "avg_return": 0.0,
                "median_return": 0.0, "max_profit": 0.0, "max_loss": 0.0,
                "max_drawdown": 0.0, "total_return": 0.0}
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, (equity - peak) / peak * 100)
    return {"code": code, "trades": len(returns),
            "win_rate": sum(value > 0 for value in returns) / len(returns) * 100,
            "avg_return": sum(returns) / len(returns), "median_return": float(pd.Series(returns).median()),
            "max_profit": max(returns), "max_loss": min(returns), "max_drawdown": max_drawdown,
            "total_return": (equity - 1) * 100}


def create_report(results: list[dict], errors: list[str], output: Path, period: str, hold_days: int) -> None:
    lines = ["# 📊 日本株バックテスト結果", "", f"- 検証期間：過去 {period}",
             f"- 保有期間：{hold_days}営業日", "- 判定ロジック：本番監視と共通",
             "- 価格系列：株式分割・配当調整済み日足",
             "- 約定仮定：シグナル翌営業日の始値で購入、指定日数後の終値で売却",
             "- 同時保有：なし（決済まで次のシグナルを無視）", "",
             "| 銘柄 | 取引数 | 勝率 | 平均 | 中央値 | 最大利益 | 最大損失 | 累積収益 | 最大DD |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        lines.append(f"| {r['code']} | {r['trades']} | {r['win_rate']:.1f}% | {r['avg_return']:+.2f}% | "
                     f"{r['median_return']:+.2f}% | {r['max_profit']:+.2f}% | {r['max_loss']:+.2f}% | "
                     f"{r['total_return']:+.2f}% | {r['max_drawdown']:.2f}% |")
    if errors:
        lines.extend(["", "## 取得エラー", ""] + [f"- {error}" for error in errors])
    lines.extend(["", "> 手数料・スリッページ・税金は未考慮です。過去の結果は将来を保証しません。"])
    output.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output", default="backtest_report.md")
    parser.add_argument("--period", default="2y")
    parser.add_argument("--hold-days", type=int, default=10)
    args = parser.parse_args(argv)
    results, errors = [], []
    for code in load_codes(args.config):
        try:
            market = download_stock(code, period=args.period, auto_adjust=True)
            results.append(summarize(code, simulate(market.frame, args.hold_days)))
        except Exception as exc:
            errors.append(str(exc))
    create_report(results, errors, Path(args.output), args.period, args.hold_days)
    print(f"backtest: {len(results)} succeeded, {len(errors)} failed")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
