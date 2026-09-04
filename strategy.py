"""Shared technical indicators and decision logic.

Both the live monitor and the backtester must use this module.  Keeping the
decision engine pure makes it straightforward to test and prevents the two
execution paths from silently drifting apart.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass(frozen=True)
class Decision:
    score: int
    signal: str
    signal_key: str
    confidence: str
    reasons: tuple[str, ...]
    volume_ratio: float

    def to_dict(self) -> dict:
        result = asdict(self)
        result["reasons"] = list(self.reasons)
        return result


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period, min_periods=period).mean()
    avg_loss = loss.rolling(period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    # A run with no losses is genuinely overbought, rather than missing data.
    return rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0).mask(
        (avg_loss == 0) & (avg_gain == 0), 50.0
    )


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    required = {"Close", "Volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"必要な列がありません: {', '.join(sorted(missing))}")

    result = df.copy()
    close = result["Close"].astype(float)
    volume = result["Volume"].astype(float)
    result["MA5"] = close.rolling(5).mean()
    result["MA25"] = close.rolling(25).mean()
    result["MA75"] = close.rolling(75).mean()
    result["RSI"] = calc_rsi(close)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    result["MACD"] = ema12 - ema26
    result["MACD_SIGNAL"] = result["MACD"].ewm(span=9, adjust=False).mean()
    result["VOL_MA20"] = volume.rolling(20).mean()
    return result


def _confidence(score: int) -> str:
    if score >= 80 or score <= 25:
        return "高"
    if score >= 65 or score <= 40:
        return "中"
    return "低"


def evaluate_rows(row: pd.Series, previous: pd.Series | None = None) -> Decision:
    score = 50
    reasons: list[str] = []
    price = float(row["Close"])
    ma5 = float(row["MA5"])
    ma25 = float(row["MA25"])
    ma75 = float(row["MA75"])
    rsi = float(row["RSI"])
    macd = float(row["MACD"])
    macd_signal = float(row["MACD_SIGNAL"])
    volume = float(row["Volume"])
    vol_ma20 = float(row["VOL_MA20"])

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

    if 45 <= rsi <= 65:
        score += 8
        reasons.append("RSIは健全な上昇圏")
    elif rsi < 30:
        score += 4
        reasons.append("RSIは売られ過ぎ水準")
    elif rsi >= 75:
        score -= 10
        reasons.append("RSIは過熱気味")

    if macd > macd_signal:
        score += 10
        reasons.append("MACDが強気")
    else:
        score -= 8
        reasons.append("MACDが弱気")

    if previous is not None:
        prev_macd = float(previous["MACD"])
        prev_signal = float(previous["MACD_SIGNAL"])
        if prev_macd <= prev_signal and macd > macd_signal:
            score += 8
            reasons.append("MACDゴールデンクロス発生")
        elif prev_macd >= prev_signal and macd < macd_signal:
            score -= 8
            reasons.append("MACDデッドクロス発生")

    volume_ratio = volume / vol_ma20 if vol_ma20 > 0 else 1.0
    if volume_ratio >= 1.5:
        score += 8
        reasons.append("出来高が20日平均の1.5倍以上")
    elif volume_ratio < 0.6:
        score -= 4
        reasons.append("出来高が低調")

    score = max(0, min(100, int(score)))

    # Risk exits take precedence over entries.  This fixes the old behaviour
    # where an overbought stock could still be labelled as a buy.
    if score <= 35:
        key, signal = "stop_loss", "🔴 損切り警戒"
    elif rsi >= 75:
        key, signal = "take_profit", "🟠 利確検討"
    elif score >= 80 and rsi < 72:
        key, signal = "buy", "🟢 強い買い候補"
    elif score >= 70:
        key, signal = "add", "🟢 買い・買い増し候補"
    elif price < ma25 and macd < macd_signal:
        key, signal = "warning", "⚠️ 下落警戒"
    else:
        key, signal = "hold", "🟡 継続監視"

    return Decision(
        score=score,
        signal=signal,
        signal_key=key,
        confidence=_confidence(score),
        reasons=tuple(reasons),
        volume_ratio=round(volume_ratio, 2),
    )


def evaluate_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[Decision | None]]:
    enriched = add_indicators(df).copy()
    decisions: list[Decision | None] = [None] * len(enriched)
    valid_columns = ["MA5", "MA25", "MA75", "RSI", "MACD", "MACD_SIGNAL", "VOL_MA20"]
    for position in range(len(enriched)):
        row = enriched.iloc[position]
        if row[valid_columns].isna().any():
            continue
        previous = enriched.iloc[position - 1] if position > 0 else None
        decisions[position] = evaluate_rows(row, previous)
    return enriched, decisions
