"""Candidate-universe acquisition and lightweight screening for V3.

The JPX monthly workbook is downloaded directly (no HTML scraping).  Metadata
acquisition is separate from quote enrichment so callers can cap each quote
batch and avoid sending thousands of requests to Yahoo Finance at once.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import ZipFile

from discovery import CandidateData


JPX_UNIVERSE_URL = (
    "https://www.jpx.co.jp/markets/statistics-equities/misc/"
    "tvdivq0000001vg2-att/data_j.xlsx"
)
TARGET_MARKETS = frozenset({"Prime", "Standard", "Growth"})
EXCLUDED_PRODUCT_MARKERS = ("ETF", "ETN", "REIT", "不動産投資信託", "インフラファンド")
CACHE_VERSION = 1


@dataclass(frozen=True)
class UniverseSecurity:
    code: str
    company_name: str
    market: str
    industry: str | None
    trading_unit: int | None
    source: str
    fetched_at: datetime
    security_type: str = "common_stock"

    def minimum_purchase_amount(self, current_price: float | None) -> float | None:
        if current_price is None or self.trading_unit is None:
            return None
        if current_price <= 0 or self.trading_unit <= 0:
            return None
        return float(current_price) * self.trading_unit


@dataclass(frozen=True)
class QuoteSnapshot:
    current_price: float | None
    average_volume: float | None = None
    volume_ratio: float | None = None


@dataclass(frozen=True)
class UniverseResult:
    securities: tuple[UniverseSecurity, ...]
    source: str
    fetched_at: datetime
    used_cache: bool = False
    used_fallback: bool = False
    warning: str | None = None


@dataclass(frozen=True)
class PrimaryFilter:
    markets: frozenset[str] = TARGET_MARKETS
    exclude_non_common: bool = True
    require_price: bool = True
    minimum_average_volume: float | None = None
    minimum_purchase_amount: float | None = None
    maximum_purchase_amount: float | None = None


DEFAULT_FALLBACK = (
    UniverseSecurity("7203", "トヨタ自動車", "Prime", "輸送用機器", 100,
                     "bundled-fallback", datetime(1970, 1, 1, tzinfo=timezone.utc)),
    UniverseSecurity("8306", "三菱ＵＦＪフィナンシャル・グループ", "Prime", "銀行業", 100,
                     "bundled-fallback", datetime(1970, 1, 1, tzinfo=timezone.utc)),
    UniverseSecurity("4597", "ソレイジア・ファーマ", "Growth", "医薬品", 100,
                     "bundled-fallback", datetime(1970, 1, 1, tzinfo=timezone.utc)),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _column_number(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference)
    if not letters:
        raise ValueError(f"Invalid XLSX cell reference: {cell_reference}")
    value = 0
    for char in letters.group(0):
        value = value * 26 + ord(char) - ord("A") + 1
    return value - 1


def _xlsx_rows(content: bytes) -> list[list[str]]:
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with ZipFile(BytesIO(content)) as workbook:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in item.findall(".//m:t", namespace))
                for item in root.findall("m:si", namespace)
            ]
        sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//m:sheetData/m:row", namespace):
            values: dict[int, str] = {}
            for cell in row.findall("m:c", namespace):
                value_node = cell.find("m:v", namespace)
                value = "" if value_node is None else value_node.text or ""
                if cell.get("t") == "s" and value:
                    value = shared[int(value)]
                values[_column_number(cell.get("r", ""))] = value.strip()
            if values:
                rows.append([values.get(index, "") for index in range(max(values) + 1)])
        return rows


def _market_and_type(label: str) -> tuple[str, str]:
    if any(marker.casefold() in label.casefold() for marker in EXCLUDED_PRODUCT_MARKERS):
        return label, "fund_or_other"
    translations = {"プライム": "Prime", "スタンダード": "Standard", "グロース": "Growth"}
    market = next((english for japanese, english in translations.items() if japanese in label), label)
    is_domestic_equity = "内国株式" in label and market in TARGET_MARKETS
    return market, "common_stock" if is_domestic_equity else "other"


def parse_jpx_workbook(content: bytes, *, fetched_at: datetime | None = None) -> list[UniverseSecurity]:
    rows = _xlsx_rows(content)
    if not rows:
        raise ValueError("JPX workbook is empty")
    header = {name: index for index, name in enumerate(rows[0])}
    required = {"コード", "銘柄名", "市場・商品区分", "33業種区分"}
    if not required.issubset(header):
        raise ValueError(f"JPX workbook columns changed: {sorted(required - header.keys())}")
    timestamp = fetched_at or _utc_now()
    securities = []
    for row in rows[1:]:
        def get(name: str) -> str:
            index = header[name]
            return row[index] if index < len(row) else ""

        code = get("コード")
        if not code:
            continue
        market, security_type = _market_and_type(get("市場・商品区分"))
        industry = get("33業種区分") or None
        if industry == "-":
            industry = None
        securities.append(UniverseSecurity(
            code=code,
            company_name=get("銘柄名"),
            market=market,
            industry=industry,
            # TSE domestic common shares use the standardized 100-share unit.
            # A future reference-data adapter can replace this derived value.
            trading_unit=100 if security_type == "common_stock" else None,
            source="JPX monthly listed issues",
            fetched_at=timestamp,
            security_type=security_type,
        ))
    if not securities:
        raise ValueError("JPX workbook contains no securities")
    return securities


def download_jpx_universe(
    *,
    url: str = JPX_UNIVERSE_URL,
    timeout: float = 30.0,
) -> list[UniverseSecurity]:
    request = Request(url, headers={"User-Agent": "japan-stock-monitor/3.0"})
    with urlopen(request, timeout=timeout) as response:
        return parse_jpx_workbook(response.read())


def _serialize(security: UniverseSecurity) -> dict:
    record = asdict(security)
    record["fetched_at"] = security.fetched_at.isoformat()
    return record


def _deserialize(record: Mapping[str, object]) -> UniverseSecurity:
    values = dict(record)
    values["fetched_at"] = datetime.fromisoformat(str(values["fetched_at"]))
    return UniverseSecurity(**values)


def save_cache(path: str | Path, securities: Sequence[UniverseSecurity], fetched_at: datetime) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "fetched_at": fetched_at.isoformat(),
        "securities": [_serialize(item) for item in securities],
    }
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def load_cache(path: str | Path) -> tuple[list[UniverseSecurity], datetime]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != CACHE_VERSION:
        raise ValueError("Unsupported universe cache version")
    fetched_at = datetime.fromisoformat(payload["fetched_at"])
    return [_deserialize(item) for item in payload["securities"]], fetched_at


def acquire_universe(
    cache_path: str | Path,
    *,
    max_age: timedelta = timedelta(days=7),
    fetcher: Callable[[], list[UniverseSecurity]] = download_jpx_universe,
    fallback: Sequence[UniverseSecurity] = DEFAULT_FALLBACK,
    now: datetime | None = None,
) -> UniverseResult:
    timestamp = now or _utc_now()
    cached: list[UniverseSecurity] | None = None
    cached_at: datetime | None = None
    try:
        cached, cached_at = load_cache(cache_path)
        if timestamp - cached_at <= max_age:
            return UniverseResult(tuple(cached), "cache", cached_at, used_cache=True)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        cached = None
        cached_at = None

    try:
        securities = fetcher()
        if not securities:
            raise ValueError("Universe fetch returned no securities")
        fetched_at = max((item.fetched_at for item in securities), default=timestamp)
        save_cache(cache_path, securities, fetched_at)
        return UniverseResult(tuple(securities), "JPX", fetched_at)
    except Exception as exc:  # transport and source-format failures must not stop V3
        warning = f"Universe fetch failed: {exc}"
        if cached is not None and cached_at is not None:
            return UniverseResult(tuple(cached), "stale-cache", cached_at, used_cache=True, warning=warning)
        refreshed = tuple(replace(item, fetched_at=timestamp) for item in fallback)
        return UniverseResult(refreshed, "fallback", timestamp, used_fallback=True, warning=warning)


def filter_equity_universe(
    securities: Iterable[UniverseSecurity],
    *,
    markets: frozenset[str] = TARGET_MARKETS,
    exclude_non_common: bool = True,
) -> list[UniverseSecurity]:
    return [
        item for item in securities
        if item.market in markets and (not exclude_non_common or item.security_type == "common_stock")
    ]


def classify_purchase_amount(amount: float | None) -> str:
    if amount is None or amount < 0:
        return "unknown"
    if amount <= 10_000:
        return "under_10k"
    if amount <= 30_000:
        return "10k_to_30k"
    if amount <= 50_000:
        return "30k_to_50k"
    return "over_50k"


def preselect_for_quote_enrichment(
    securities: Iterable[UniverseSecurity],
    *,
    markets: frozenset[str] = TARGET_MARKETS,
    limit: int = 300,
) -> list[UniverseSecurity]:
    """Bound the next quote-fetch stage without performing network requests."""
    if limit < 0:
        raise ValueError("limit must be zero or greater")
    eligible = filter_equity_universe(securities, markets=markets)
    return sorted(eligible, key=lambda item: item.code)[:limit]


def primary_filter(
    securities: Iterable[UniverseSecurity],
    quotes: Mapping[str, QuoteSnapshot],
    config: PrimaryFilter = PrimaryFilter(),
) -> list[UniverseSecurity]:
    results = []
    for item in filter_equity_universe(
        securities,
        markets=config.markets,
        exclude_non_common=config.exclude_non_common,
    ):
        quote = quotes.get(item.code)
        if config.require_price and (quote is None or quote.current_price is None or quote.current_price <= 0):
            continue
        if (
            config.minimum_average_volume is not None
            and (quote is None or quote.average_volume is None or quote.average_volume < config.minimum_average_volume)
        ):
            continue
        amount = item.minimum_purchase_amount(quote.current_price if quote else None)
        if config.minimum_purchase_amount is not None and (amount is None or amount < config.minimum_purchase_amount):
            continue
        if config.maximum_purchase_amount is not None and (amount is None or amount > config.maximum_purchase_amount):
            continue
        results.append(item)
    return results


def to_raw_candidates(
    securities: Iterable[UniverseSecurity],
    quotes: Mapping[str, QuoteSnapshot],
    *,
    technical_scores: Mapping[str, float] | None = None,
) -> list[CandidateData]:
    """Adapter from universe output to discovery.py raw_candidates."""
    technical_scores = technical_scores or {}
    candidates = []
    for item in securities:
        quote = quotes.get(item.code, QuoteSnapshot(None))
        candidates.append(CandidateData(
            code=item.code,
            name=item.company_name,
            price=quote.current_price,
            lot_size=item.trading_unit,
            average_volume=quote.average_volume,
            volume_ratio=quote.volume_ratio,
            technical_score=technical_scores.get(item.code),
        ))
    return candidates
