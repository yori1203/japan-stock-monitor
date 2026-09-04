"""Market-data access with normalization and bounded retries."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class MarketData:
    frame: pd.DataFrame
    fetched_at: datetime
    data_as_of: str
    source: str = "Yahoo Finance (yfinance)"


def _normalize(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("株価データを取得できませんでした")
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    required = {"Open", "Close", "Volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"株価データに必要な列がありません: {', '.join(sorted(missing))}")
    frame = frame.sort_index()
    frame = frame.loc[~frame.index.duplicated(keep="last")]
    return frame


def download_stock(
    code: str,
    *,
    period: str = "1y",
    auto_adjust: bool = False,
    attempts: int = 3,
    retry_seconds: float = 2.0,
) -> MarketData:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            f"{code}: yfinance がインストールされていません。requirements.txt を適用してください"
        ) from exc

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = yf.download(
                f"{code}.T",
                period=period,
                interval="1d",
                auto_adjust=auto_adjust,
                actions=False,
                progress=False,
                threads=False,
                timeout=20,
            )
            frame = _normalize(raw)
            fetched_at = datetime.now(JST)
            latest_index = pd.Timestamp(frame.index[-1])
            data_as_of = latest_index.strftime("%Y-%m-%d")
            return MarketData(frame, fetched_at, data_as_of)
        except Exception as exc:  # yfinance raises several transport exceptions
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_seconds * attempt)
    raise RuntimeError(f"{code}: {attempts}回のデータ取得に失敗しました: {last_error}")
