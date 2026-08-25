import json
import csv
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf


CONFIG_FILE = "config.json"
REPORT_FILE = "report.md"
SIGNALS_FILE = "signals.csv"


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def calc_rsi(close, period=14):
    delta = close.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))

    return rsi


def add_indicators(df):
    df = df.copy()

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA25"] = df["Close"].rolling(25).mean()
    df["MA75"] = df["Close"].rolling(75).mean()

    df["RSI"] = calc_rsi(df["Close"])

    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()

    df["MACD"] = ema12 - ema26
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

    df["VOL_MA20"] = df["Volume"].rolling(20).mean()

    return df


def score_stock(row, previous):
    score = 50
    reasons = []

    price = float(row["Close"])
    ma5 = float(row["MA5"])
    ma25 = float(row["MA25"])
    ma75 = float(row["MA75"])
    rsi = float(row["RSI"])
    macd = float(row["MACD"])
    macd_signal = float(row["MACD_SIGNAL"])
    volume = float(row["Volume"])
    vol_ma20 = float(row["VOL_MA20"])

    # トレンド
    if price > ma25:
        score += 10
        reasons.append("株価が25日移動平均線より上")
    else:
        score -= 10
        reasons.append("株価が25日移動平均線より下")

    if ma5 > ma25:
        score += 8
        reasons.append("短期トレンド上向き")
    else:
        score -= 8
        reasons.append("短期トレンド弱め")

    if ma25 > ma75:
        score += 8
        reasons.append("中期上昇トレンド")
    else:
        score -= 8
        reasons.append("中期トレンド弱め")

    # RSI
    if 45 <= rsi <= 65:
        score += 8
        reasons.append("RSIは健全な上昇圏")
    elif rsi < 30:
        score += 4
        reasons.append("RSIは売られ過ぎ水準")
    elif rsi >= 75:
        score -= 10
        reasons.append("RSIは過熱気味")

    # MACD
    if macd > macd_signal:
        score += 10
        reasons.append("MACDが強気")
    else:
        score -= 8
        reasons.append("MACDが弱気")

    # MACDクロス
    if previous is not None:
        prev_macd = float(previous["MACD"])
        prev_signal = float(previous["MACD_SIGNAL"])

        if prev_macd <= prev_signal and macd > macd_signal:
            score += 8
            reasons.append("MACDゴールデンクロス発生")

        if prev_macd >= prev_signal and macd < macd_signal:
            score -= 8
            reasons.append("MACDデッドクロス発生")

    # 出来高
    if vol_ma20 > 0:
        volume_ratio = volume / vol_ma20

        if volume_ratio >= 1.5:
            score += 8
            reasons.append("出来高が20日平均の1.5倍以上")
        elif volume_ratio < 0.6:
            score -= 4
            reasons.append("出来高が低調")
    else:
        volume_ratio = 1.0

    score = max(0, min(100, score))

    return score, reasons, volume_ratio


def make_signal(score, rsi, price, ma25, macd, macd_signal):
    if score >= 80 and rsi < 72:
        return "🟢 強い買い候補"

    if score >= 70:
        return "🟢 買い・買い増し候補"

    if rsi >= 75 and score >= 55:
        return "🟠 利確検討"

    if score <= 35:
        return "🔴 損切り警戒"

    if price < ma25 and macd < macd_signal:
        return "⚠️ 下落警戒"

    return "🟡 継続監視"


def confidence(score):
    if score >= 80 or score <= 25:
        return "高"

    if score >= 65 or score <= 40:
        return "中"

    return "低"


def fetch_stock(code):
    ticker = f"{code}.T"

    df = yf.download(
        ticker,
        period="1y",
        interval="1d",
        auto_adjust=False,
        progress=False
    )

    if df.empty:
        raise ValueError("株価データを取得できませんでした")

    # yfinanceのMultiIndex対策
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = add_indicators(df)
    df = df.dropna()

    if len(df) < 2:
        raise ValueError("分析に必要なデータが不足しています")

    latest = df.iloc[-1]
    previous = df.iloc[-2]

    score, reasons, volume_ratio = score_stock(latest, previous)

    price = float(latest["Close"])
    ma5 = float(latest["MA5"])
    ma25 = float(latest["MA25"])
    ma75 = float(latest["MA75"])
    rsi = float(latest["RSI"])
    macd = float(latest["MACD"])
    macd_signal = float(latest["MACD_SIGNAL"])

    signal = make_signal(
        score,
        rsi,
        price,
        ma25,
        macd,
        macd_signal
    )

    return {
        "code": code,
        "price": round(price, 1),
        "score": int(score),
        "signal": signal,
        "confidence": confidence(score),
        "rsi": round(rsi, 1),
        "ma5": round(ma5, 1),
        "ma25": round(ma25, 1),
        "ma75": round(ma75, 1),
        "macd": round(macd, 2),
        "macd_signal": round(macd_signal, 2),
        "volume_ratio": round(volume_ratio, 2),
        "reasons": reasons
    }


def save_signal(result):
    exists = Path(SIGNALS_FILE).exists()

    with open(SIGNALS_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not exists:
            writer.writerow([
                "date",
                "code",
                "price",
                "score",
                "signal",
                "confidence",
                "rsi"
            ])

        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            result["code"],
            result["price"],
            result["score"],
            result["signal"],
            result["confidence"],
            result["rsi"]
        ])


def create_report(results):
    now = datetime.now()

    lines = [
        "# 📊 日本株 自動監視レポート",
        "",
        f"更新日時：{now.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 保有・重点監視銘柄",
        ""
    ]

    for r in results:
        lines.extend([
            f"### {r['code']}",
            "",
            f"**{r['signal']}**",
            "",
            f"- 現在値：{r['price']}円",
            f"- 総合スコア：**{r['score']} / 100**",
            f"- 確信度：**{r['confidence']}**",
            f"- RSI：{r['rsi']}",
            f"- 5日移動平均：{r['ma5']}円",
            f"- 25日移動平均：{r['ma25']}円",
            f"- 75日移動平均：{r['ma75']}円",
            f"- 出来高倍率：{r['volume_ratio']}倍",
            "",
            "**判定理由**",
        ])

        for reason in r["reasons"]:
            lines.append(f"- {reason}")

        lines.extend(["", "---", ""])

    lines.extend([
        "## シグナルの意味",
        "",
        "- 🟢 強い買い候補：複数条件が強気",
        "- 🟢 買い・買い増し候補：上昇条件が優勢",
        "- 🟡 継続監視：決定的な方向感なし",
        "- 🟠 利確検討：過熱感を警戒",
        "- ⚠️ 下落警戒：チャート悪化",
        "- 🔴 損切り警戒：複数の弱気条件",
        "",
        "> この判定は投資助言ではなく、監視・分析用シグナルです。"
    ])

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    config = load_config()

    codes = []

    for stock in config.get("portfolio", []):
        codes.append(stock["code"])

    for stock in config.get("watchlist", []):
        if isinstance(stock, dict):
            codes.append(stock["code"])
        else:
            codes.append(str(stock))

    codes = list(dict.fromkeys(codes))

    results = []

    print("=== Japan Stock Monitor ===")

    for code in codes:
        print(f"分析中: {code}")

        try:
            result = fetch_stock(code)
            results.append(result)
            save_signal(result)

            print(
                code,
                result["signal"],
                result["score"]
            )

        except Exception as e:
            print(f"{code}: エラー - {e}")

    if results:
        create_report(results)

    print("監視完了")


if __name__ == "__main__":
    main()
