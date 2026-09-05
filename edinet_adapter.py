"""Official EDINET API v2 adapter and XBRL normalisation for V3."""
from __future__ import annotations

import csv
import io
import json
import os
import time
import urllib.parse
import urllib.error
import urllib.request
import unicodedata
import zipfile
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from xml.etree import ElementTree

EDINET_API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
EDINET_CODE_LIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
USEFUL_DOC_TYPES = frozenset({"120", "130", "140", "150", "160"})

XBRL_TAGS: Mapping[str, tuple[str, ...]] = {
    "revenue": ("NetSales", "Revenue", "OperatingRevenue", "RevenueIFRS"),
    "operating_income": ("OperatingIncome", "OperatingProfitLoss", "OperatingProfitLossIFRS"),
    "ordinary_income": ("OrdinaryIncome", "OrdinaryIncomeLoss"),
    "net_income": ("ProfitLoss", "NetIncome", "ProfitLossAttributableToOwnersOfParent"),
    "total_assets": ("Assets", "AssetsIFRS"),
    "equity": ("NetAssets", "Equity", "EquityAttributableToOwnersOfParent"),
    "cash_and_equivalents": ("CashAndCashEquivalents", "CashAndCashEquivalentsIFRS"),
    "interest_bearing_debt": ("InterestBearingDebt", "BondsAndBorrowings", "Borrowings"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
    "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities", "CashFlowsFromUsedInInvestingActivities"),
    "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities", "CashFlowsFromUsedInFinancingActivities"),
    "eps": ("BasicEarningsLossPerShare", "BasicEarningsLossPerShareIFRS"),
    "bps": ("NetAssetsPerShare", "EquityPerShareAttributableToOwnersOfParent"),
    "shares_outstanding": ("NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfShares", "NumberOfIssuedSharesTotalNumberOfShares"),
}


@dataclass(frozen=True)
class EdinetCodeEntry:
    edinet_code: str
    stock_code: str
    company_name: str
    industry: str | None = None


@dataclass(frozen=True)
class EdinetFinancialData:
    code: str
    edinet_code: str | None = None
    doc_id: str | None = None
    document_type: str | None = None
    document_name: str | None = None
    submitted_at: str | None = None
    accounting_standard: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    revenue: float | None = None
    operating_income: float | None = None
    ordinary_income: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    equity: float | None = None
    cash_and_equivalents: float | None = None
    interest_bearing_debt: float | None = None
    operating_cash_flow: float | None = None
    investing_cash_flow: float | None = None
    financing_cash_flow: float | None = None
    eps: float | None = None
    bps: float | None = None
    shares_outstanding: float | None = None
    previous_shares_outstanding: float | None = None
    previous_equity: float | None = None
    previous_total_assets: float | None = None
    previous_free_cash_flow: float | None = None
    fetched_at: str = ""
    source: str = "EDINET API v2"

    @property
    def shares_outstanding_growth(self) -> float | None:
        if not self.previous_shares_outstanding:
            return None
        return (self.shares_outstanding / self.previous_shares_outstanding - 1) if self.shares_outstanding is not None else None

    @property
    def free_cash_flow(self) -> float | None:
        if self.operating_cash_flow is None or self.investing_cash_flow is None:
            return None
        return self.operating_cash_flow + self.investing_cash_flow


@dataclass(frozen=True)
class EdinetResult:
    status: str
    data: EdinetFinancialData | None = None
    reason: str | None = None
    cache_hit: bool = False


@dataclass(frozen=True)
class EdinetConfig:
    timeout: float = 30
    max_retries: int = 2
    retry_delay: float = 1
    rate_limit_delay: float = 0.5
    cache_ttl_hours: float = 24


@dataclass(frozen=True)
class HttpResponse:
    body: bytes
    status: int = 200
    content_type: str = ""


@dataclass(frozen=True)
class CodeMapDiagnostics:
    http_status: int | None = None
    content_type: str | None = None
    archive_member: str | None = None
    encoding: str | None = None
    header_row: int | None = None
    data_rows: int = 0
    mapped_rows: int = 0
    reason: str | None = None


class HttpTransport(Protocol):
    def get(self, url: str, timeout: float) -> bytes | HttpResponse: ...


class UrllibTransport:
    def get(self, url: str, timeout: float) -> HttpResponse:
        request = urllib.request.Request(url, headers={"User-Agent": "japan-stock-monitor-v3/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResponse(response.read(), int(response.status), response.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            message = _safe_edinet_message(exc.read())
            raise RuntimeError(f"EDINET HTTP {exc.code}: {message}") from None


def normalize_stock_code(value: str) -> str:
    digits = "".join(char for char in str(value) if char.isdigit())
    return digits[:4] if len(digits) >= 4 else digits


def _canonical_header(value: str) -> str:
    return unicodedata.normalize("NFKC", value).replace(" ", "").replace("\u3000", "").lstrip("\ufeff").casefold()


def _decode_csv(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("cp932", errors="replace"), "cp932-replace"


def _parse_edinet_code_list(content: bytes) -> tuple[dict[str, EdinetCodeEntry], CodeMapDiagnostics]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        name = next(item for item in archive.namelist() if item.lower().endswith(".csv"))
        raw = archive.read(name)
    text, encoding = _decode_csv(raw)
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return {}, CodeMapDiagnostics(archive_member=name, encoding=encoding, reason="CSV is empty")
    aliases = {"edinet": ("EDINETコード",), "stock": ("証券コード",)}
    header_index = None
    canonical: list[str] = []
    for row_index, row in enumerate(rows[:20]):
        candidate = [_canonical_header(value) for value in row]
        if all(any(_canonical_header(label) in candidate for label in labels) for labels in aliases.values()):
            header_index, canonical = row_index, candidate
            break
    if header_index is None:
        return {}, CodeMapDiagnostics(archive_member=name, encoding=encoding, reason="required CSV headers not found")
    def column(*labels):
        for label in labels:
            key = _canonical_header(label)
            if key in canonical: return canonical.index(key)
        return -1
    indexes = {"edinet": column("EDINETコード"), "stock": column("証券コード"),
               "name": column("提出者名"), "industry": column("提出者業種")}
    result = {}
    required_max = max(indexes["edinet"], indexes["stock"], indexes["name"])
    for row in rows[header_index + 1:]:
        if required_max >= len(row): continue
        stock = normalize_stock_code(row[indexes["stock"]])
        edinet_code = row[indexes["edinet"]].strip()
        if len(stock) == 4 and edinet_code:
            industry = row[indexes["industry"]].strip() if 0 <= indexes["industry"] < len(row) else None
            result[stock] = EdinetCodeEntry(edinet_code, stock, row[indexes["name"]].strip(), industry or None)
    diagnostics = CodeMapDiagnostics(archive_member=name, encoding=encoding, header_row=header_index + 1,
                                     data_rows=max(0, len(rows) - header_index - 1), mapped_rows=len(result),
                                     reason=None if result else "no valid stock-code rows")
    return result, diagnostics


def parse_edinet_code_list(content: bytes) -> dict[str, EdinetCodeEntry]:
    return _parse_edinet_code_list(content)[0]


def _safe_edinet_message(content: bytes) -> str:
    try:
        payload = json.loads(content.decode("utf-8", errors="replace"))
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        return str(metadata.get("message") or payload.get("message") or "request rejected")[:300]
    except (ValueError, AttributeError):
        return "request rejected"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


def _numeric(text: str | None, scale: str | None) -> float | None:
    try:
        value = float(str(text).replace(",", ""))
        return value * (10 ** int(scale or "0"))
    except (TypeError, ValueError, OverflowError):
        return None


def parse_xbrl(content: bytes, code: str, edinet_code: str | None = None, doc_id: str | None = None) -> EdinetFinancialData:
    root = ElementTree.fromstring(content)
    contexts: dict[str, tuple[str | None, str | None]] = {}
    for node in root.iter():
        if _local_name(node.tag) != "context": continue
        start = end = None
        for child in node.iter():
            name = _local_name(child.tag)
            if name == "startDate": start = child.text
            elif name in ("endDate", "instant"): end = child.text
        contexts[node.attrib.get("id", "")] = (start, end)
    candidates: dict[str, list[tuple[str | None, str | None, float]]] = {key: [] for key in XBRL_TAGS}
    standard = "IFRS" if any("ifrs" in _local_name(node.tag).lower() for node in root.iter()) else "J-GAAP"
    for node in root.iter():
        name = _local_name(node.tag)
        value = _numeric(node.text, node.attrib.get("scale"))
        if value is None: continue
        for field_name, tags in XBRL_TAGS.items():
            if name in tags:
                start, end = contexts.get(node.attrib.get("contextRef", ""), (None, None))
                candidates[field_name].append((start, end, value))
    periods = [end for values in candidates.values() for _, end, _ in values if end]
    latest = max(periods) if periods else None
    selected = {}
    previous = {}
    for field_name, values in candidates.items():
        current_values = [value for _, end, value in values if end == latest] or [value for _, _, value in values]
        selected[field_name] = current_values[0] if current_values else None
        older = sorted(((end, value) for _, end, value in values if end and end != latest), reverse=True)
        previous[field_name] = older[0][1] if older else None
    starts = [start for values in candidates.values() for start, end, _ in values if end == latest and start]
    return EdinetFinancialData(code=code, edinet_code=edinet_code, doc_id=doc_id, accounting_standard=standard,
        period_start=min(starts) if starts else None, period_end=latest,
        previous_shares_outstanding=previous["shares_outstanding"], previous_equity=previous["equity"],
        previous_total_assets=previous["total_assets"], fetched_at=datetime.now(timezone.utc).isoformat(),
        **selected)


class EdinetAdapter:
    def __init__(self, api_key: str | None = None, *, transport: HttpTransport | None = None,
                 config: EdinetConfig = EdinetConfig(), cache_dir: str | Path = ".cache/edinet",
                 sleeper: Callable[[float], None] = time.sleep):
        self.api_key = api_key if api_key is not None else os.getenv("EDINET_API_KEY")
        self.transport = transport or UrllibTransport(); self.config = config
        self.cache_dir = Path(cache_dir); self.sleeper = sleeper
        self.last_http_status: int | None = None
        self.last_content_type: str | None = None
        self.code_map_diagnostics = CodeMapDiagnostics(reason="not fetched")

    def _get(self, endpoint: str, params: Mapping[str, str]) -> bytes:
        query = urllib.parse.urlencode({**params, "Subscription-Key": self.api_key or ""})
        return self._raw_get(f"{EDINET_API_BASE}/{endpoint}?{query}")

    def _raw_get(self, url: str) -> bytes:
        error_type = "unknown"
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.transport.get(url, self.config.timeout)
                if isinstance(response, HttpResponse):
                    content = response.body
                    self.last_http_status = response.status
                    self.last_content_type = response.content_type
                    if not 200 <= response.status < 300:
                        raise RuntimeError(f"EDINET HTTP {response.status}: {_safe_edinet_message(content)}")
                else:
                    content = response
                    self.last_http_status = 200
                    self.last_content_type = None
                if self.config.rate_limit_delay > 0: self.sleeper(self.config.rate_limit_delay)
                return content
            except Exception as exc:
                error_type = type(exc).__name__
                if attempt < self.config.max_retries: self.sleeper(self.config.retry_delay * 2 ** attempt)
        # Never include the requested URL: it contains Subscription-Key.
        raise RuntimeError(f"EDINET request failed ({error_type})")

    def find_latest_documents(self, codes: Sequence[str], *, end_date: date | None = None,
                              lookback_days: int = 190) -> dict[str, dict]:
        """Scan each date once for all target companies, newest document wins."""
        entries = self.fetch_code_map()
        wanted = {entries[normalize_stock_code(code)].edinet_code: normalize_stock_code(code)
                  for code in codes if normalize_stock_code(code) in entries}
        found: dict[str, dict] = {}
        last = end_date or date.today()
        for offset in range(max(lookback_days, 0)):
            day = last - timedelta(days=offset)
            listing_path = self.cache_dir / "document-lists" / f"{day.isoformat()}.json"
            try:
                listing = json.loads(listing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                listing = self._decode_api_json(self._get("documents.json", {"date": day.isoformat(), "type": "2"}))
                listing_path.parent.mkdir(parents=True, exist_ok=True)
                listing_path.write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")
            for item in listing.get("results", []):
                code = wanted.get(item.get("edinetCode"))
                if code and code not in found and str(item.get("docTypeCode")) in USEFUL_DOC_TYPES and item.get("xbrlFlag") == "1":
                    found[code] = item
            if len(found) == len(wanted): break
        return found

    def _decode_api_json(self, content: bytes) -> dict[str, Any]:
        payload = json.loads(content)
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        status = str(metadata.get("status") or metadata.get("statusCode") or "200")
        if status not in ("200", "0"):
            message = str(metadata.get("message") or "EDINET API error")[:300]
            raise RuntimeError(f"EDINET API status {status}: {message}")
        return payload

    def fetch_document(self, code: str, document: Mapping[str, object]) -> EdinetResult:
        if not self.api_key: return EdinetResult("unavailable", reason="EDINET_API_KEY is not set")
        normalized = normalize_stock_code(code)
        path = self.cache_dir / f"{normalized}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8")); fetched = datetime.fromisoformat(raw["fetched_at"])
            if (datetime.now(timezone.utc) - fetched <= timedelta(hours=self.config.cache_ttl_hours)
                    and raw.get("data", {}).get("doc_id") == str(document.get("docID"))):
                names = {item.name for item in fields(EdinetFinancialData)}
                return EdinetResult("ok", EdinetFinancialData(**{k:v for k,v in raw["data"].items() if k in names}), cache_hit=True)
        except (OSError, ValueError, KeyError, TypeError): pass
        try:
            doc_id = str(document["docID"])
            archive = zipfile.ZipFile(io.BytesIO(self._get(f"documents/{doc_id}", {"type": "1"})))
            xbrl_name = next(name for name in archive.namelist() if name.lower().endswith(".xbrl"))
            data = parse_xbrl(archive.read(xbrl_name), normalized, str(document.get("edinetCode") or ""), doc_id)
            data = EdinetFinancialData(**{**asdict(data), "document_type": str(document.get("docTypeCode") or ""),
                "document_name": str(document.get("docDescription") or ""), "submitted_at": str(document.get("submitDateTime") or "")})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": asdict(data)}, ensure_ascii=False), encoding="utf-8")
            return EdinetResult("ok", data)
        except Exception as exc:
            return EdinetResult("error", reason=f"document_fetch_failed ({type(exc).__name__})")

    def fetch_code_map(self, force: bool = False) -> dict[str, EdinetCodeEntry]:
        path = self.cache_dir / "code-list.json"
        if not force:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                fetched = datetime.fromisoformat(payload["fetched_at"])
                if datetime.now(timezone.utc) - fetched <= timedelta(hours=self.config.cache_ttl_hours):
                    entries = {key: EdinetCodeEntry(**value) for key, value in payload["entries"].items()}
                    if entries:
                        self.code_map_diagnostics = CodeMapDiagnostics(
                            mapped_rows=len(entries), reason="loaded from cache"
                        )
                        return entries
            except (OSError, ValueError, KeyError, TypeError): pass
        content = self._raw_get(EDINET_CODE_LIST_URL)
        entries, parsed = _parse_edinet_code_list(content)
        self.code_map_diagnostics = CodeMapDiagnostics(
            http_status=self.last_http_status, content_type=self.last_content_type,
            archive_member=parsed.archive_member, encoding=parsed.encoding,
            header_row=parsed.header_row, data_rows=parsed.data_rows,
            mapped_rows=parsed.mapped_rows, reason=parsed.reason,
        )
        if not entries:
            raise RuntimeError(f"EDINET code list parsed zero entries ({parsed.reason or 'unknown reason'})")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "entries": {k: asdict(v) for k,v in entries.items()}}, ensure_ascii=False), encoding="utf-8")
        return entries

    def fetch(self, code: str, *, target_date: date | None = None) -> EdinetResult:
        if not self.api_key: return EdinetResult("unavailable", reason="EDINET_API_KEY is not set")
        normalized = normalize_stock_code(code); path = self.cache_dir / f"{normalized}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8")); fetched = datetime.fromisoformat(raw["fetched_at"])
            if datetime.now(timezone.utc) - fetched <= timedelta(hours=self.config.cache_ttl_hours):
                names = {item.name for item in fields(EdinetFinancialData)}
                return EdinetResult("ok", EdinetFinancialData(**{k:v for k,v in raw["data"].items() if k in names}), cache_hit=True)
        except (OSError, ValueError, KeyError, TypeError): pass
        try:
            entry = self.fetch_code_map().get(normalized)
            if not entry: return EdinetResult("unavailable", reason="EDINET code not found")
            day = target_date or date.today()
            listing = self._decode_api_json(self._get("documents.json", {"date": day.isoformat(), "type": "2"}))
            docs = [item for item in listing.get("results", []) if item.get("edinetCode") == entry.edinet_code and str(item.get("docTypeCode")) in USEFUL_DOC_TYPES and item.get("xbrlFlag") == "1"]
            if not docs: return EdinetResult("unavailable", reason="useful XBRL document not found")
            doc = max(docs, key=lambda item: item.get("submitDateTime", ""))
            archive = zipfile.ZipFile(io.BytesIO(self._get(f"documents/{doc['docID']}", {"type": "1"})))
            xbrl_name = next(name for name in archive.namelist() if name.lower().endswith(".xbrl"))
            data = parse_xbrl(archive.read(xbrl_name), normalized, entry.edinet_code, doc["docID"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "data": asdict(data)}, ensure_ascii=False), encoding="utf-8")
            return EdinetResult("ok", data)
        except Exception as exc:
            return EdinetResult("error", reason=f"edinet_fetch_failed ({type(exc).__name__})")
