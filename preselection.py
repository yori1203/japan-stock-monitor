"""Fast, failure-tolerant primary screening for the V3 discovery pipeline."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence

import pandas as pd

from discovery import CandidateData
from universe import TARGET_MARKETS, UniverseSecurity, filter_equity_universe


PRESELECTION_WEIGHTS: Mapping[str, float] = {
    "liquidity": 30.0,
    "trend": 25.0,
    "affordability": 20.0,
    "data_quality": 15.0,
    "stability": 10.0,
}


@dataclass(frozen=True)
class MarketSnapshot:
    code: str
    current_price: float
    average_volume: float
    average_trading_value: float
    price_change_20d: float | None
    price_change_60d: float | None
    ma20: float | None
    ma60: float | None
    distance_from_52w_high: float | None
    zero_volume_ratio: float
    daily_volatility: float | None
    observation_count: int
    data_as_of: str
    source: str = "Yahoo Finance (yfinance)"


@dataclass(frozen=True)
class PreselectedCandidate:
    security: UniverseSecurity
    snapshot: MarketSnapshot
    minimum_purchase_amount: float | None
    investment_category: str
    preselection_score: float
    component_scores: Mapping[str, float]


@dataclass(frozen=True)
class FailedSymbol:
    code: str
    failure_reason: str


@dataclass(frozen=True)
class PreselectionConfig:
    markets: frozenset[str] = TARGET_MARKETS
    minimum_average_trading_value: float = 1_000_000.0
    maximum_zero_volume_ratio: float = 0.20
    maximum_purchase_amount: float | None = 1_000_000.0
    minimum_observations: int = 60
    top_n: int = 400
    batch_size: int = 100
    weights: Mapping[str, float] = field(default_factory=lambda: dict(PRESELECTION_WEIGHTS))


@dataclass(frozen=True)
class PreselectionStats:
    input_count: int
    eligible_count: int
    excluded_count: int
    failed_count: int
    returned_count: int
    cache_hit_count: int
    execution_time: float


@dataclass(frozen=True)
class PreselectionResult:
    universe: tuple[UniverseSecurity, ...]
    eligible_candidates: tuple[PreselectedCandidate, ...]
    ranked_candidates: tuple[PreselectedCandidate, ...]
    top_preselected: tuple[PreselectedCandidate, ...]
    failed_symbols: tuple[FailedSymbol, ...]
    stats: PreselectionStats


class BatchSnapshotProvider(Protocol):
    def fetch_batch(self, securities: Sequence[UniverseSecurity]) -> Mapping[str, MarketSnapshot]: ...


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def classify_investment_amount(amount: float | None) -> str:
    if not _valid(amount) or float(amount) < 0:
        return "unknown"
    if float(amount) <= 10_000:
        return "under_10k"
    if float(amount) <= 30_000:
        return "10k_to_30k"
    if float(amount) <= 50_000:
        return "30k_to_50k"
    if float(amount) <= 100_000:
        return "50k_to_100k"
    return "over_100k"


def calculate_snapshot(code: str, frame: pd.DataFrame, *, source: str = "Yahoo Finance (yfinance)") -> MarketSnapshot:
    if frame.empty or not {"Close", "Volume"}.issubset(frame.columns):
        raise ValueError("price data is empty or missing Close/Volume")
    clean = frame[["Close", "Volume"]].copy().dropna(subset=["Close"])
    clean["Close"] = pd.to_numeric(clean["Close"], errors="coerce")
    clean["Volume"] = pd.to_numeric(clean["Volume"], errors="coerce").fillna(0)
    clean = clean.dropna(subset=["Close"])
    if clean.empty or float(clean["Close"].iloc[-1]) <= 0:
        raise ValueError("valid current price is unavailable")
    close, volume = clean["Close"].astype(float), clean["Volume"].astype(float)
    current = float(close.iloc[-1])
    recent_volume = volume.tail(20)
    average_volume = float(recent_volume.mean()) if len(recent_volume) else 0.0
    average_trading_value = float((close.tail(20) * recent_volume).mean())

    def change(days: int) -> float | None:
        if len(close) <= days or float(close.iloc[-days - 1]) <= 0:
            return None
        return current / float(close.iloc[-days - 1]) - 1.0

    daily_returns = close.pct_change().replace([math.inf, -math.inf], pd.NA).dropna().tail(60)
    high_52w = float(close.tail(252).max()) if len(close) else 0.0
    return MarketSnapshot(
        code=code,
        current_price=current,
        average_volume=average_volume,
        average_trading_value=average_trading_value,
        price_change_20d=change(20),
        price_change_60d=change(60),
        ma20=float(close.tail(20).mean()) if len(close) >= 20 else None,
        ma60=float(close.tail(60).mean()) if len(close) >= 60 else None,
        distance_from_52w_high=current / high_52w - 1.0 if high_52w > 0 else None,
        zero_volume_ratio=float((volume.tail(60) <= 0).mean()) if len(volume) else 1.0,
        daily_volatility=float(daily_returns.std()) if len(daily_returns) >= 10 else None,
        observation_count=len(clean),
        data_as_of=pd.Timestamp(clean.index[-1]).strftime("%Y-%m-%d"),
        source=source,
    )


def _liquidity_score(snapshot: MarketSnapshot) -> float:
    value = max(snapshot.average_trading_value, 1.0)
    return _clamp((math.log10(value) - 5.0) / 4.0 * 100.0)


def _trend_score(snapshot: MarketSnapshot) -> float:
    values: list[float] = []
    if snapshot.price_change_20d is not None:
        values.append(_clamp(50.0 + snapshot.price_change_20d * 250.0))
    if snapshot.price_change_60d is not None:
        values.append(_clamp(50.0 + snapshot.price_change_60d * 150.0))
    if snapshot.ma20 is not None and snapshot.ma20 > 0:
        values.append(70.0 if snapshot.current_price >= snapshot.ma20 else 30.0)
    if snapshot.ma60 is not None and snapshot.ma60 > 0:
        values.append(70.0 if snapshot.current_price >= snapshot.ma60 else 30.0)
    if snapshot.distance_from_52w_high is not None:
        values.append(_clamp(100.0 + snapshot.distance_from_52w_high * 125.0))
    return sum(values) / len(values) if values else 0.0


def _affordability_score(amount: float | None) -> float:
    if amount is None:
        return 0.0
    if 8_000 <= amount <= 30_000:
        return 100.0
    if amount < 8_000:
        return 70.0 + _clamp(amount / 8_000 * 30.0)
    if amount <= 100_000:
        return _clamp(100.0 - (amount - 30_000) / 70_000 * 40.0)
    return _clamp(60.0 - (amount - 100_000) / 900_000 * 60.0)


def _quality_score(snapshot: MarketSnapshot) -> float:
    completeness = sum(value is not None for value in (
        snapshot.price_change_20d, snapshot.price_change_60d, snapshot.ma20,
        snapshot.ma60, snapshot.distance_from_52w_high, snapshot.daily_volatility,
    )) / 6
    observations = min(snapshot.observation_count / 252.0, 1.0)
    return _clamp((completeness * 0.7 + observations * 0.3) * 100.0)


def _stability_score(snapshot: MarketSnapshot) -> float:
    if snapshot.daily_volatility is None:
        return 0.0
    return _clamp(100.0 - snapshot.daily_volatility * 2_500.0)


def score_preselection(
    security: UniverseSecurity,
    snapshot: MarketSnapshot,
    weights: Mapping[str, float] = PRESELECTION_WEIGHTS,
) -> PreselectedCandidate:
    amount = security.minimum_purchase_amount(snapshot.current_price)
    components = {
        "liquidity": _liquidity_score(snapshot),
        "trend": _trend_score(snapshot),
        "affordability": _affordability_score(amount),
        "data_quality": _quality_score(snapshot),
        "stability": _stability_score(snapshot),
    }
    denominator = sum(max(float(weights.get(key, 0)), 0.0) for key in components)
    score = sum(components[key] * max(float(weights.get(key, 0)), 0.0) for key in components)
    total = score / denominator if denominator else 0.0
    return PreselectedCandidate(
        security=security,
        snapshot=snapshot,
        minimum_purchase_amount=amount,
        investment_category=classify_investment_amount(amount),
        preselection_score=round(_clamp(total), 2),
        component_scores={key: round(value, 2) for key, value in components.items()},
    )


def exclusion_reason(candidate: PreselectedCandidate, config: PreselectionConfig) -> str | None:
    snapshot = candidate.snapshot
    if snapshot.observation_count < config.minimum_observations:
        return "insufficient_data"
    if snapshot.average_trading_value < config.minimum_average_trading_value:
        return "low_trading_value"
    if snapshot.zero_volume_ratio > config.maximum_zero_volume_ratio:
        return "mostly_zero_volume"
    if candidate.minimum_purchase_amount is None:
        return "purchase_amount_unavailable"
    if config.maximum_purchase_amount is not None and candidate.minimum_purchase_amount > config.maximum_purchase_amount:
        return "purchase_amount_too_high"
    return None


def _snapshot_to_dict(snapshot: MarketSnapshot) -> dict:
    return asdict(snapshot)


def _load_daily_cache(path: str | Path, cache_date: date) -> dict[str, MarketSnapshot]:
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("date") != cache_date.isoformat():
            return {}
        return {code: MarketSnapshot(**value) for code, value in payload.get("snapshots", {}).items()}
    except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save_daily_cache(path: str | Path, cache_date: date, snapshots: Mapping[str, MarketSnapshot]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"date": cache_date.isoformat(), "snapshots": {code: _snapshot_to_dict(value) for code, value in snapshots.items()}}
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def run_preselection(
    universe: Iterable[UniverseSecurity],
    provider: BatchSnapshotProvider,
    *,
    config: PreselectionConfig = PreselectionConfig(),
    cache_path: str | Path = ".cache/preselection.json",
    today: date | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> PreselectionResult:
    started = clock()
    raw = tuple(universe)
    target = tuple(filter_equity_universe(raw, markets=config.markets))
    cache_date = today or datetime.now(timezone.utc).date()
    snapshots = _load_daily_cache(cache_path, cache_date)
    cache_hits = sum(item.code in snapshots for item in target)
    failures: list[FailedSymbol] = []
    missing = [item for item in target if item.code not in snapshots]
    for offset in range(0, len(missing), max(config.batch_size, 1)):
        batch = missing[offset:offset + max(config.batch_size, 1)]
        try:
            fetched = provider.fetch_batch(batch)
        except Exception as exc:
            failures.extend(FailedSymbol(item.code, f"batch_fetch_failed: {exc}") for item in batch)
            continue
        for item in batch:
            snapshot = fetched.get(item.code)
            if snapshot is None:
                failures.append(FailedSymbol(item.code, "price_data_unavailable"))
            else:
                snapshots[item.code] = snapshot
    _save_daily_cache(cache_path, cache_date, snapshots)

    eligible: list[PreselectedCandidate] = []
    excluded_count = len(raw) - len(target)
    for item in target:
        snapshot = snapshots.get(item.code)
        if snapshot is None:
            continue
        candidate = score_preselection(item, snapshot, config.weights)
        if exclusion_reason(candidate, config) is None:
            eligible.append(candidate)
        else:
            excluded_count += 1
    ranked = sorted(eligible, key=lambda item: (-item.preselection_score, item.security.code))
    top = ranked[:max(config.top_n, 0)]
    elapsed = max(clock() - started, 0.0)
    stats = PreselectionStats(
        input_count=len(raw), eligible_count=len(eligible), excluded_count=excluded_count,
        failed_count=len(failures), returned_count=len(top), cache_hit_count=cache_hits,
        execution_time=round(elapsed, 4),
    )
    return PreselectionResult(raw, tuple(eligible), tuple(ranked), tuple(top), tuple(failures), stats)


def to_discovery_candidates(candidates: Iterable[PreselectedCandidate]) -> list[CandidateData]:
    """Lightweight hand-off interface; financial fields remain unavailable."""
    return [CandidateData(
        code=item.security.code,
        name=item.security.company_name,
        price=item.snapshot.current_price,
        lot_size=item.security.trading_unit,
        average_volume=item.snapshot.average_volume,
        volume_ratio=None,
        technical_score=item.preselection_score,
    ) for item in candidates]


class YFinanceBatchProvider:
    """One yfinance request per bounded batch, never one request per symbol."""

    def __init__(self, *, period: str = "1y", timeout: int = 30):
        self.period = period
        self.timeout = timeout

    def fetch_batch(self, securities: Sequence[UniverseSecurity]) -> Mapping[str, MarketSnapshot]:
        import yfinance as yf

        ticker_to_code = {f"{item.code}.T": item.code for item in securities}
        if not ticker_to_code:
            return {}
        raw = yf.download(
            list(ticker_to_code), period=self.period, interval="1d", group_by="ticker",
            auto_adjust=False, actions=False, progress=False, threads=True, timeout=self.timeout,
        )
        result: dict[str, MarketSnapshot] = {}
        for ticker, code in ticker_to_code.items():
            try:
                frame = raw[ticker] if isinstance(raw.columns, pd.MultiIndex) else raw
                result[code] = calculate_snapshot(code, frame)
            except (KeyError, TypeError, ValueError):
                continue
        return result
