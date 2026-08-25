import json
from pathlib import Path

import pandas as pd
import yfinance as yf


CONFIG_FILE = "config.json"
OUTPUT_FILE = "backtest_report.md"

HOLD_DAYS = 10
HISTORY_PERIOD = "2y"


def load_codes():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    items = config.get("portfolio", []) + config.get("watchlist", [])
    return list(dict.fromkeys(item["code"] for item in items))


def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def prepare_data(code):
    ticker = f"{code}.T"

    df = yf.download(
        ticker,
        period=HISTORY_PERIOD,
        auto_adjust=True,
        progress=False
    )

    if df.empty:
        raise ValueError("株価データを取得できませんでした")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"]
    volume = df["Volume"]

    df["MA5"] = close.rolling(5).mean()
    df["MA25"] = close.rolling(25).mean()
    df["MA75"] = close.rolling(75).mean()
    df["RSI"] = calc_rsi(close)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

    df["VOL20"] = volume.rolling(20).mean()
    df["VOL_RATIO"] = volume / df["VOL20"]

    return df.dropna().copy()


def create_signal(df):
    score = pd.Series(0, index=df.index, dtype=float)

    score += (df["Close"] > df["MA25"]).astype(int) * 20
    score += (df["MA5"] > df["MA25"]).astype(int) * 15
    score += (df["MA25"] > df["MA75"]).astype(int) * 15

    score += (
        (df["RSI"] >= 45) &
        (df["RSI"] <= 70)
    ).astype(int) * 20

    score += (
        df["MACD"] > df["MACD_SIGNAL"]
    ).astype(int) * 20

    score += (
        df["VOL_RATIO"] >= 1.2
    ).astype(int) * 10

    df["SCORE"] = score
    df["BUY_SIGNAL"] = df["SCORE"] >= 70

    return df


def backtest_stock(code):
    df = prepare_data(code)
    df = create_signal(df)

    trades = []

    for i in range(len(df) - HOLD_DAYS):
        if not df["BUY_SIGNAL"].iloc[i]:
            continue

        buy_price = float(df["Close"].iloc[i])
        sell_price = float(df["Close"].iloc[i + HOLD_DAYS])

        profit_pct = (
            (sell_price - buy_price) / buy_price
        ) * 100

        trades.append({
            "date": df.index[i],
            "buy": buy_price,
            "sell": sell_price,
            "return": profit_pct
        })

     if not trades:
        return {
            "code": code,
            "trades": 0,
            "wins": 0,
            "win_rate": 0,
            "avg_return": 0,
            "median_return": 0,
            "max_profit": 0,
            "max_loss": 0,
            "expectancy": 0,
            "max_drawdown": 0
        }

    returns = [t["return"] for t in trades]

    wins = sum(r > 0 for r in returns)
    win_rate = wins / len(returns) * 100

    avg_return = sum(returns) / len(returns)
    median_return = float(pd.Series(returns).median())

    max_profit = max(returns)
    max_loss = min(returns)

    winning_returns = [r for r in returns if r > 0]
    losing_returns = [r for r in returns if r <= 0]

    avg_win = (
        sum(winning_returns) / len(winning_returns)
        if winning_returns else 0
    )

    avg_loss = (
        sum(losing_returns) / len(losing_returns)
        if losing_returns else 0
    )

    win_probability = wins / len(returns)
    loss_probability = 1 - win_probability

    expectancy = (
        win_probability * avg_win
        + loss_probability * avg_loss
    )

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0

    for r in returns:
        equity *= 1 + (r / 100)
        peak = max(peak, equity)

        drawdown = (equity - peak) / peak * 100
        max_drawdown = min(max_drawdown, drawdown)

    return {
        "code": code,
        "trades": len(trades),
        "wins": wins,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "median_return": median_return,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "expectancy": expectancy,
        "max_drawdown": max_drawdown
    }


def create_report(results):
    lines = [
        "# 📊 日本株バックテスト結果",
        "",
        f"- 検証期間: 過去 {HISTORY_PERIOD}",
        f"- シグナル後保有期間: {HOLD_DAYS}営業日",
        "",
                "| 銘柄 | シグナル数 | 勝率 | 平均 | 中央値 | 最大利益 | 最大損失 | 期待値 | 最大DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    ]

        for r in results:
        lines.append(
            f'| {r["code"]} | '
            f'{r["trades"]} | '
            f'{r["win_rate"]:.1f}% | '
            f'{r["avg_return"]:+.2f}% | '
            f'{r["median_return"]:+.2f}% | '
            f'{r["max_profit"]:+.2f}% | '
            f'{r["max_loss"]:+.2f}% | '
            f'{r["expectancy"]:+.2f}% | '
            f'{r["max_drawdown"]:.2f}% |'
        )

    Path(OUTPUT_FILE).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )


def main():
    codes = load_codes()
    results = []

    print("=== Backtest Start ===")

    for code in codes:
        try:
            print(f"検証中: {code}")
            result = backtest_stock(code)
            results.append(result)

            print(
                code,
                f'勝率 {result["win_rate"]:.1f}%',
                f'平均 {result["avg_return"]:+.2f}%'
            )

        except Exception as e:
            print(f"{code}: エラー - {e}")

    create_report(results)

    print("バックテスト完了")


if __name__ == "__main__":
    main()
