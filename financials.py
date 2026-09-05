"""Failure-tolerant financial enrichment for the V3 discovery pipeline.

The module deliberately stays disconnected from the V2 monitor.  Providers are
adapters, while caching, retries, timeouts and scoring live in this layer.
"""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Protocol, Sequence

from preselection import PreselectedCandidate


FINANCIAL_SCORE_WEIGHTS: Mapping[str, float] = {
    "growth": 30.0,
    "profitability": 20.0,
    "valuation": 20.0,
    "financial_health": 20.0,
    "shareholder_return": 10.0,
}

CORE_QUALITY_FIELDS = (
    "revenue", "revenue_growth_yoy", "operating_income", "operating_margin",
    "net_income", "eps", "per", "pbr", "roe", "equity_ratio", "market_cap",
)


@dataclass(frozen=True)
class FinancialData:
    code: str
    revenue: float | None = None
    revenue_growth_yoy: float | None = None
    revenue_growth_3y_cagr: float | None = None
    operating_income: float | None = None
    operating_income_growth_yoy: float | None = None
    operating_margin: float | None = None
    net_income: float | None = None
    eps: float | None = None
    eps_growth_yoy: float | None = None
    book_value_per_share: float | None = None
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    roa: float | None = None
    equity_ratio: float | None = None
    cash_and_equivalents: float | None = None
    total_debt: float | None = None
    free_cash_flow: float | None = None
    market_cap: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    forward_revenue: float | None = None
    forward_eps: float | None = None
    earnings_growth_forecast: float | None = None
    analyst_revision: float | None = None
    dilution_risk: float | None = None
    previous_equity_ratio: float | None = None
    previous_total_debt: float | None = None
    consecutive_loss_years: int | None = None
    source: str = "unknown"
    period_end: str | None = None
    fetched_at: str = ""


@dataclass(frozen=True)
class FinancialCandidate:
    code: str
    company_name: str
    market: str
    minimum_purchase_amount: float | None
    preselection_score: float
    financial_score: float
    growth_score: float
    profitability_score: float
    valuation_score: float
    financial_health_score: float
    shareholder_return_score: float
    financial_data_quality_score: float
    risk_flags: tuple[str, ...]
    score_reasons: tuple[str, ...]
    fetched_at: str
    financial_data: FinancialData


@dataclass(frozen=True)
class FinancialFailure:
    code: str
    failure_reason: str


@dataclass(frozen=True)
class FinancialConfig:
    max_candidates: int = 400
    top_n: int = 75
    batch_size: int = 20
    max_workers: int = 4
    request_timeout: float = 20.0
    max_retries: int = 2
    retry_delay: float = 1.0
    batch_delay: float = 1.0
    cache_ttl_hours: float = 24.0
    weights: Mapping[str, float] = field(default_factory=lambda: dict(FINANCIAL_SCORE_WEIGHTS))


@dataclass(frozen=True)
class FinancialStats:
    input_count: int
    attempted_count: int
    success_count: int
    failed_count: int
    returned_count: int
    cache_hit_count: int
    execution_time: float
    average_fetch_time: float


@dataclass(frozen=True)
class FinancialResult:
    top_preselected: tuple[PreselectedCandidate, ...]
    enriched_candidates: tuple[FinancialCandidate, ...]
    financially_ranked: tuple[FinancialCandidate, ...]
    top_financial_candidates: tuple[FinancialCandidate, ...]
    failed_symbols: tuple[FinancialFailure, ...]
    stats: FinancialStats


class FinancialAdapter(Protocol):
    def fetch(self, code: str) -> FinancialData: ...


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _mean_available(values: Sequence[float | None], default: float = 50.0) -> float:
    available = [float(value) for value in values if value is not None]
    return sum(available) / len(available) if available else default


def _linear(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    if high == low:
        return 50.0
    return _clamp((value - low) / (high - low) * 100.0)


def growth_score(data: FinancialData) -> float:
    scores = [
        _linear(data.revenue_growth_yoy, -0.10, 0.30),
        _linear(data.operating_income_growth_yoy, -0.20, 0.50),
        _linear(data.eps_growth_yoy, -0.20, 0.50),
        _linear(data.revenue_growth_3y_cagr, -0.05, 0.20),
        _linear(data.earnings_growth_forecast, -0.10, 0.30),
    ]
    return round(_mean_available(scores), 2)


def profitability_score(data: FinancialData) -> float:
    return round(_mean_available([
        _linear(data.operating_margin, -0.05, 0.20),
        _linear(data.roe, -0.05, 0.20),
        _linear(data.roa, -0.03, 0.10),
    ]), 2)


def valuation_score(data: FinancialData, market: str = "") -> float:
    values: list[float | None] = []
    if data.per is not None:
        if data.per <= 0:
            values.append(25.0)
        elif data.per <= 10:
            values.append(90.0)
        elif data.per <= 20:
            values.append(75.0)
        elif data.per <= 40:
            values.append(55.0)
        elif data.per <= 80:
            values.append(35.0)
        else:
            values.append(15.0)
    if data.pbr is not None:
        values.append(90.0 if 0 < data.pbr <= 1 else 75.0 if data.pbr <= 2 else 50.0 if data.pbr <= 5 else 20.0)
    result = _mean_available(values)
    # A genuine high-growth company is not treated like a no-growth mature firm.
    best_growth = max(data.revenue_growth_yoy or -1.0, data.earnings_growth_forecast or -1.0)
    if best_growth >= 0.20 and data.per is not None and 30 < data.per <= 80:
        result = max(result, 50.0 if "Growth" in market else 45.0)
    return round(_clamp(result), 2)


def financial_health_score(data: FinancialData) -> float:
    equity = _linear(data.equity_ratio, 0.10, 0.70)
    cash_debt = None
    if data.cash_and_equivalents is not None and data.total_debt is not None:
        denominator = max(abs(data.total_debt), 1.0)
        cash_debt = _linear(data.cash_and_equivalents / denominator, 0.0, 2.0)
    fcf = None
    if data.free_cash_flow is not None and data.revenue not in (None, 0):
        fcf = _linear(data.free_cash_flow / abs(data.revenue), -0.10, 0.15)
    return round(_mean_available([equity, cash_debt, fcf]), 2)


def shareholder_return_score(data: FinancialData) -> float:
    # Neutral defaults avoid making growth/reinvestment companies automatic losers.
    dividend = _linear(data.dividend_yield, 0.0, 0.05) if data.dividend_yield is not None else None
    payout = None
    if data.payout_ratio is not None:
        payout = 80.0 if 0.20 <= data.payout_ratio <= 0.50 else 60.0 if 0 <= data.payout_ratio <= 0.70 else 25.0
    return round(_mean_available([dividend, payout], default=50.0), 2)


def data_quality_score(data: FinancialData, now: datetime | None = None) -> float:
    completeness = sum(_number(getattr(data, name)) is not None for name in CORE_QUALITY_FIELDS) / len(CORE_QUALITY_FIELDS)
    freshness = 50.0
    reference = now or datetime.now(timezone.utc)
    # Prefer the accounting period over the retrieval timestamp: a response
    # fetched today can still contain an old fiscal statement.
    timestamp = data.period_end or data.fetched_at
    if timestamp:
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = max((reference - parsed).days, 0)
            freshness = _clamp(100.0 - age / 730.0 * 100.0)
        except ValueError:
            freshness = 25.0
    consistency_checks: list[bool] = []
    if data.revenue is not None:
        consistency_checks.append(data.revenue >= 0)
    if data.market_cap is not None:
        consistency_checks.append(data.market_cap >= 0)
    if data.equity_ratio is not None:
        consistency_checks.append(-0.5 <= data.equity_ratio <= 1.0)
    consistency = (sum(consistency_checks) / len(consistency_checks) * 100.0) if consistency_checks else 50.0
    return round(_clamp(completeness * 70.0 + freshness * 0.20 + consistency * 0.10), 2)


def risk_flags(data: FinancialData, quality: float) -> tuple[str, ...]:
    flags: list[str] = []
    if data.operating_income is not None and data.operating_income < 0:
        flags.append("operating_loss")
    if data.consecutive_loss_years is not None and data.consecutive_loss_years >= 2:
        flags.append("consecutive_losses")
    if data.free_cash_flow is not None and data.revenue and data.free_cash_flow / abs(data.revenue) < -0.10:
        flags.append("large_negative_fcf")
    if data.equity_ratio is not None and data.equity_ratio < 0:
        flags.append("negative_equity")
    if data.previous_equity_ratio is not None and data.equity_ratio is not None and data.previous_equity_ratio - data.equity_ratio >= 0.10:
        flags.append("declining_equity_ratio")
    if data.previous_total_debt and data.total_debt is not None and data.total_debt / data.previous_total_debt >= 1.5:
        flags.append("rapid_debt_increase")
    if (data.per is not None and (data.per > 100 or data.per < 0)) or (data.pbr is not None and data.pbr > 10):
        flags.append("extreme_valuation")
    if quality < 50:
        flags.append("insufficient_data_quality")
    if data.dilution_risk is not None and data.dilution_risk >= 0.7:
        flags.append("dilution_risk")
    return tuple(flags)


def score_financial_candidate(candidate: PreselectedCandidate, data: FinancialData,
                              weights: Mapping[str, float] = FINANCIAL_SCORE_WEIGHTS,
                              now: datetime | None = None) -> FinancialCandidate:
    growth = growth_score(data)
    profitability = profitability_score(data)
    valuation = valuation_score(data, candidate.security.market)
    health = financial_health_score(data)
    returns = shareholder_return_score(data)
    quality = data_quality_score(data, now)
    components = {"growth": growth, "profitability": profitability, "valuation": valuation,
                  "financial_health": health, "shareholder_return": returns}
    denominator = sum(max(float(weights.get(key, 0)), 0) for key in components)
    total = sum(value * max(float(weights.get(key, 0)), 0) for key, value in components.items())
    score = _clamp(total / denominator if denominator else 0.0)
    reasons: list[str] = []
    if max(data.revenue_growth_yoy or -1, data.revenue_growth_3y_cagr or -1) >= 0.15:
        reasons.append("strong_revenue_growth")
    if data.operating_margin is not None and data.operating_margin >= 0.15:
        reasons.append("strong_operating_margin")
    if data.operating_income_growth_yoy is not None and data.operating_income_growth_yoy >= 0.15:
        reasons.append("operating_income_improving")
    if data.per is not None and data.per > 40:
        reasons.append("high_per")
    elif data.per is not None and 0 < data.per <= 15:
        reasons.append("reasonable_per")
    if data.equity_ratio is not None and data.equity_ratio >= 0.50:
        reasons.append("strong_equity_ratio")
    if data.free_cash_flow is not None:
        reasons.append("positive_fcf" if data.free_cash_flow > 0 else "weak_fcf")
    if quality < 60:
        reasons.append("limited_financial_data")
    return FinancialCandidate(
        candidate.security.code, candidate.security.company_name, candidate.security.market,
        candidate.minimum_purchase_amount, candidate.preselection_score, round(score, 2),
        growth, profitability, valuation, health, returns, quality,
        risk_flags(data, quality), tuple(reasons), data.fetched_at, data,
    )


def _load_cache(path: str | Path, now: datetime, ttl: timedelta) -> dict[str, FinancialData]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {}
    result: dict[str, FinancialData] = {}
    valid_names = {item.name for item in fields(FinancialData)}
    for code, raw in payload.get("financials", {}).items():
        try:
            fetched = datetime.fromisoformat(str(raw.get("fetched_at", "")).replace("Z", "+00:00"))
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if now - fetched <= ttl:
                result[code] = FinancialData(**{key: value for key, value in raw.items() if key in valid_names})
        except (TypeError, ValueError):
            continue
    return result


def _save_cache(path: str | Path, values: Mapping[str, FinancialData]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps({"financials": {code: asdict(value) for code, value in values.items()}}, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _fetch_with_retry(adapter: FinancialAdapter, code: str, config: FinancialConfig,
                      sleeper: Callable[[float], None]) -> tuple[FinancialData | None, str | None, float]:
    started = time.perf_counter()
    last_error = "unknown_error"
    for attempt in range(config.max_retries + 1):
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(adapter.fetch, code)
        try:
            data = future.result(timeout=config.request_timeout)
            executor.shutdown(wait=True)
            return data, None, time.perf_counter() - started
        except FutureTimeoutError:
            last_error = "timeout"
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            executor.shutdown(wait=True)
        if attempt < config.max_retries:
            sleeper(config.retry_delay * (2 ** attempt))
    return None, last_error, time.perf_counter() - started


def run_financial_enrichment(
    top_preselected: Iterable[PreselectedCandidate], adapter: FinancialAdapter, *,
    config: FinancialConfig = FinancialConfig(), cache_path: str | Path = ".cache/financials.json",
    now: datetime | None = None, sleeper: Callable[[float], None] = time.sleep,
) -> FinancialResult:
    started = time.perf_counter()
    source = tuple(top_preselected)
    selected = source[:max(config.max_candidates, 0)]
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    cache = _load_cache(cache_path, reference, timedelta(hours=config.cache_ttl_hours))
    cache_hits = sum(item.security.code in cache for item in selected)
    failures: list[FinancialFailure] = []
    fetch_durations: list[float] = []
    missing = [item for item in selected if item.security.code not in cache]
    batch_size = max(config.batch_size, 1)
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset:offset + batch_size]
        with ThreadPoolExecutor(max_workers=max(1, min(config.max_workers, len(batch)))) as pool:
            jobs = {pool.submit(_fetch_with_retry, adapter, item.security.code, config, sleeper): item for item in batch}
            for job, item in jobs.items():
                data, error, duration = job.result()
                fetch_durations.append(duration)
                if data is None:
                    failures.append(FinancialFailure(item.security.code, error or "financial_data_unavailable"))
                else:
                    if not data.fetched_at:
                        data = FinancialData(**{**asdict(data), "fetched_at": reference.isoformat()})
                    cache[item.security.code] = data
        if offset + batch_size < len(missing) and config.batch_delay > 0:
            sleeper(config.batch_delay)
    _save_cache(cache_path, cache)
    enriched = [score_financial_candidate(item, cache[item.security.code], config.weights, reference)
                for item in selected if item.security.code in cache]
    ranked = sorted(enriched, key=lambda item: (-item.financial_score, -item.preselection_score, item.code))
    top = ranked[:max(config.top_n, 0)]
    elapsed = time.perf_counter() - started
    stats = FinancialStats(len(source), len(selected), len(enriched), len(failures), len(top), cache_hits,
                           round(elapsed, 4), round(sum(fetch_durations) / len(fetch_durations), 4) if fetch_durations else 0.0)
    return FinancialResult(source, tuple(enriched), tuple(ranked), tuple(top), tuple(failures), stats)


def _first(mapping: Mapping, *names: str):
    for name in names:
        value = mapping.get(name)
        if _number(value) is not None:
            return float(value)
    return None


def _statement_values(statement, labels: Sequence[str]) -> list[float]:
    if statement is None or getattr(statement, "empty", True):
        return []
    for label in labels:
        if label in statement.index:
            return [float(value) for value in statement.loc[label].tolist() if _number(value) is not None]
    return []


def _growth(values: Sequence[float], periods: int = 1) -> float | None:
    if len(values) <= periods or values[periods] == 0:
        return None
    return values[0] / values[periods] - 1.0


class YahooFinanceAdapter:
    """Yahoo adapter isolated so EDINET/TDnet adapters can be added later."""

    def fetch(self, code: str) -> FinancialData:
        import yfinance as yf

        ticker = yf.Ticker(f"{code}.T")
        info = ticker.get_info()
        income = ticker.income_stmt
        balance = ticker.balance_sheet
        cashflow = ticker.cashflow
        revenues = _statement_values(income, ("Total Revenue", "Operating Revenue"))
        operating = _statement_values(income, ("Operating Income",))
        net_income = _statement_values(income, ("Net Income", "Net Income Common Stockholders"))
        diluted_eps = _statement_values(income, ("Diluted EPS", "Basic EPS"))
        cash = _statement_values(balance, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"))
        debt = _statement_values(balance, ("Total Debt",))
        equity = _statement_values(balance, ("Stockholders Equity", "Total Equity Gross Minority Interest"))
        assets = _statement_values(balance, ("Total Assets",))
        fcf = _statement_values(cashflow, ("Free Cash Flow",))
        equity_ratio = equity[0] / assets[0] if equity and assets and assets[0] else None
        previous_equity_ratio = equity[1] / assets[1] if len(equity) > 1 and len(assets) > 1 and assets[1] else None
        consecutive_losses = 0
        for value in operating:
            if value < 0:
                consecutive_losses += 1
            else:
                break
        return FinancialData(
            code=code, revenue=revenues[0] if revenues else _first(info, "totalRevenue"),
            revenue_growth_yoy=_growth(revenues) if len(revenues) > 1 else _first(info, "revenueGrowth"),
            revenue_growth_3y_cagr=(revenues[0] / revenues[3]) ** (1 / 3) - 1 if len(revenues) > 3 and revenues[0] > 0 and revenues[3] > 0 else None,
            operating_income=operating[0] if operating else _first(info, "operatingIncome"),
            operating_income_growth_yoy=_growth(operating),
            operating_margin=_first(info, "operatingMargins") or (operating[0] / revenues[0] if operating and revenues and revenues[0] else None),
            net_income=net_income[0] if net_income else _first(info, "netIncomeToCommon"),
            eps=_first(info, "trailingEps") or (diluted_eps[0] if diluted_eps else None), eps_growth_yoy=_growth(diluted_eps),
            book_value_per_share=_first(info, "bookValue"), per=_first(info, "trailingPE"), pbr=_first(info, "priceToBook"),
            roe=_first(info, "returnOnEquity"), roa=_first(info, "returnOnAssets"), equity_ratio=equity_ratio,
            cash_and_equivalents=cash[0] if cash else _first(info, "totalCash"), total_debt=debt[0] if debt else _first(info, "totalDebt"),
            free_cash_flow=fcf[0] if fcf else _first(info, "freeCashflow"), market_cap=_first(info, "marketCap"),
            dividend_yield=_first(info, "dividendYield"), payout_ratio=_first(info, "payoutRatio"),
            forward_revenue=_first(info, "revenueEstimate"), forward_eps=_first(info, "forwardEps"),
            earnings_growth_forecast=_first(info, "earningsGrowth"), analyst_revision=None,
            previous_equity_ratio=previous_equity_ratio, previous_total_debt=debt[1] if len(debt) > 1 else None,
            consecutive_loss_years=consecutive_losses, source="Yahoo Finance (yfinance)",
            period_end=str(income.columns[0].date()) if income is not None and not income.empty else None,
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )
