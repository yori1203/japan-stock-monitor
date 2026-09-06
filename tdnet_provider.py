"""Bounded public TDnet provider, disabled until use permission is established.

Public visibility is not a data-reuse license. No authentication bypass, paid
endpoint, proxy rotation, PDF downloading, or background scheduler is included.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import date
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urljoin

from tdnet_events import JST, TDnetEvent, normalize_event, timestamp
from tdnet_adapter import MockTDnetAdapter, TDnetResult

SOURCE = "jpx_tdnet_public"
BASE = "https://www.release.tdnet.info/inbs/"
POLICY_WARNING = "TDnet公開情報の自動取得・保存に必要な利用許諾を未確認（無断転用・複製禁止）。取得は無効です。"


@dataclass(frozen=True)
class ProviderResult:
    status: str
    events: tuple[TDnetEvent, ...] = ()
    source: str = SOURCE
    is_mock: bool = False
    fetched_at: str = ""
    target_dates: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()
    cache_hits: int = 0
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def provider_name(self):
        return self.source


class TDnetProvider(ABC):
    @abstractmethod
    def fetch_events(self, *, dates, codes=None):
        raise NotImplementedError

    def fetch_events_for_codes(self, codes, *, dates):
        return self.fetch_events(dates=dates, codes=codes)

    def fetch_events_for_date(self, target_date, *, codes=None):
        return self.fetch_events(dates=[target_date], codes=codes)


@dataclass(frozen=True)
class AccessConfig:
    interval: float = 10.
    timeout: float = 10.
    retries: int = 1
    backoff: float = 10.
    max_requests: int = 2
    max_pages: int = 1
    max_dates: int = 1
    current_day_ttl: float = 1800.

    def __post_init__(self):
        import math
        if not all(math.isfinite(v) for v in (self.interval, self.timeout, self.backoff, self.current_day_ttl)):
            raise ValueError("access limits must be finite")
        if not (5 <= self.interval <= 60 and 1 <= self.timeout <= 20 and 5 <= self.backoff <= 30):
            raise ValueError("unsafe request interval/timeout/backoff")
        if self.retries not in (0, 1, 2) or not 1 <= self.max_requests <= 4 or not 1 <= self.max_pages <= 2 or not 1 <= self.max_dates <= 2 or self.current_day_ttl < 1800:
            raise ValueError("public access budget exceeded")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr": self.row = {}
        if tag == "td" and self.row is not None:
            self.cell = next((v for v in attrs.get("class", "").split() if v.startswith("kj")), None)
            if self.cell: self.row[self.cell] = ""
        if tag in ("del", "s", "strike"): self.ignored += 1
        if tag == "a" and self.cell == "kjTitle" and not self.ignored:
            self.row["href"] = attrs.get("href", "")

    def handle_endtag(self, tag):
        if tag in ("del", "s", "strike"): self.ignored = max(0, self.ignored - 1)
        if tag == "td": self.cell = None
        if tag == "tr":
            if self.row and any(k.startswith("kj") for k in self.row): self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell and self.row is not None and not self.ignored:
            self.row[self.cell] += data


def parse_public_page(html, target_date, fetched_at):
    day = date.fromisoformat(str(target_date))
    date_label = f"{day.year}年{day.month:02d}月{day.day:02d}日"
    if "適時開示情報閲覧サービス" not in html or date_label not in html:
        raise ValueError("HTML layout or target date changed")
    parser = PageParser()
    parser.feed(html)
    total = re.search(r"全\s*([\d,]+)\s*件", html)
    if not parser.rows and not ("開示情報はありません" in html or "開示された情報はありません" in html or (total and int(total[1].replace(",", "")) == 0)):
        raise ValueError("HTML contains neither disclosure rows nor an explicit empty marker")
    records, warnings = {}, []
    for row in parser.rows:
        code = row.get("kjCode", "").strip()
        if re.fullmatch(r"[0-9A-Z]{4}0", code): code = code[:4]
        href = row.get("href", "")
        title = row.get("kjTitle", "").strip()
        company = row.get("kjName", "").strip()
        clock = row.get("kjTime", "").strip()
        if not (re.fullmatch(r"[0-9A-Z]{4}", code) and re.fullmatch(r"\d{2}:\d{2}", clock)
                and re.fullmatch(r"\d{10,24}\.pdf", href) and title and company):
            warnings.append("項目欠落・削除済み・未知の文書URLを含む行を除外")
            continue
        try:
            published = timestamp(f"{day}T{clock}:00+09:00").isoformat()
        except ValueError:
            warnings.append("開示時刻が不正な行を除外"); continue
        identifier = href.removesuffix(".pdf")
        key = f"{day}:{code}:{identifier}"
        records[key] = dict(code=code, company_name=company, published_at=published, title=title,
                            category=None, raw_reference=urljoin(BASE, href), disclosure_id=identifier,
                            source=SOURCE, fetched_at=fetched_at, is_mock=False)
    pages = sorted(set(re.findall(r"I_list_(\d{3})_" + day.strftime("%Y%m%d") + r"\.html", html)))
    if total and parser.rows and not records:
        raise ValueError("all disclosure rows failed validation")
    return list(records.values()), [int(p) for p in pages], warnings


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url, timeout):
    # No redirect following, cookies, browser impersonation, or alternative hosts.
    request = Request(url, headers={"User-Agent": "JapanStockMonitor-V3/1.0 (manual low-volume validation)"})
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000: raise ValueError("response exceeds size limit")
            return response.status, body.decode("utf-8-sig"), dict(response.headers)
    except HTTPError as exc:
        return exc.code, "", dict(exc.headers)


class FreePublicTDnetProvider(TDnetProvider):
    def __init__(self, cache_dir=".cache/tdnet-public", *, permission_reference=None,
                 config=AccessConfig(), transport=http_get, now=time.time, sleep=time.sleep):
        self.cache = Path(cache_dir)
        # Must identify an actual permission permitting collection/cache; not a
        # URL to the public homepage. No permission is inferred from availability.
        self.permission_reference = permission_reference
        self.config, self.transport, self.now, self.sleep = config, transport, now, sleep

    def fetch_events(self, *, dates, codes=None):
        try:
            dates = tuple(dict.fromkeys(str(date.fromisoformat(str(d))) for d in dates))
        except (ValueError, TypeError):
            return ProviderResult("unavailable", fetched_at=self._iso_now(), warnings=("対象日が不正",))
        codes = tuple(dict.fromkeys(str(c) for c in codes)) if codes is not None else ()
        fetched = timestamp(self._iso_now()).isoformat()
        base = dict(fetched_at=fetched, target_dates=dates, codes=codes)
        if not self.permission_reference:
            return ProviderResult("unavailable", warnings=(POLICY_WARNING,), **base)
        if not dates or len(dates) > self.config.max_dates:
            return ProviderResult("unavailable", warnings=("対象日数がアクセス上限外",), **base)
        self.cache.mkdir(parents=True, exist_ok=True)
        lock = self.cache / "fetch.lock"
        try:
            with lock.open("x") as handle: handle.write(fetched)
        except FileExistsError:
            return ProviderResult("unavailable", warnings=("取得ロックあり。並列取得せず終了（異常終了時は手動確認）",), **base)
        try:
            return self._fetch(dates, codes, base)
        except (OSError, ValueError, TypeError, KeyError):
            return ProviderResult("error", failure_count=1, warnings=("キャッシュまたはレスポンス処理エラー。保存済みページは保持",), **base)
        finally:
            lock.unlink(missing_ok=True)

    def _iso_now(self):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.now(), timezone.utc).isoformat()

    def _fetch(self, dates, codes, base):
        state_path = self.cache / "access.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        events, warnings = {}, []
        requests = hits = success = failures = 0
        incomplete = False
        today = timestamp(self._iso_now()).astimezone(JST).date()
        for day in dates:
            pages, seen = [1], set()
            while pages:
                page = pages.pop(0)
                if page in seen: continue
                if len(seen) >= self.config.max_pages:
                    incomplete = True; warnings.append("ページ上限で中断。未取得ページあり"); break
                seen.add(page)
                path = self.cache / f"{day}-{page:03d}.json"
                cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
                valid = cached and cached.get("version") == 1 and cached.get("source") == SOURCE and cached.get("date") == day and cached.get("page") == page
                valid = valid and (date.fromisoformat(day) < today or self.now() - cached["saved_at"] < self.config.current_day_ttl)
                if valid:
                    payload = cached; hits += 1
                else:
                    if not 0 <= (today - date.fromisoformat(day)).days <= 30:
                        incomplete = True; warnings.append("公式公開31日範囲外。推測取得なし"); break
                    if state.get("cooldown_until", 0) > self.now():
                        incomplete = True; warnings.append("アクセス停止期間中（403/429等）。再試行なし"); break
                    payload = None
                    for attempt in range(self.config.retries + 1):
                        if requests >= self.config.max_requests:
                            warnings.append("実通信リクエスト上限に到達"); break
                        delay = max(0., state.get("next_request_at", 0) - self.now(), self.config.backoff * (2 ** (attempt - 1)) if attempt else 0.)
                        if delay > 60:
                            warnings.append("長い待機を避けて終了。次回に再開"); break
                        if delay: self.sleep(delay)
                        state["next_request_at"] = self.now() + self.config.interval
                        atomic_json(state_path, state)
                        requests += 1
                        try:
                            status, body, headers = self.transport(BASE + f"I_list_{page:03d}_{day.replace('-', '')}.html", self.config.timeout)
                        except (TimeoutError, URLError, OSError):
                            status, body, headers = 0, "", {}
                        if status == 200:
                            try:
                                records, linked, row_warnings = parse_public_page(body, day, self._iso_now())
                            except ValueError:
                                warnings.append("HTML/レスポンス変更を検出。空データ扱いにせず停止"); failures += 1; break
                            payload = dict(version=1, source=SOURCE, date=day, page=page, saved_at=self.now(), records=records, pages=linked, warnings=row_warnings)
                            atomic_json(path, payload)
                            break
                        failures += 1
                        warnings.append(f"HTTP {status}" if status else "timeout/network error")
                        if status in (403, 429):
                            seconds = 86400 if status == 403 else 3600
                            retry_after = next((v for k, v in headers.items() if k.lower() == "retry-after"), "")
                            if status == 429 and retry_after:
                                try: seconds = max(seconds, int(retry_after))
                                except ValueError:
                                    try: seconds = max(seconds, parsedate_to_datetime(retry_after).timestamp() - self.now())
                                    except (ValueError, TypeError): pass
                            state["cooldown_until"] = self.now() + seconds
                            atomic_json(state_path, state)
                            break
                        if status != 0 and not 500 <= status <= 599: break
                    if payload is None:
                        incomplete = True
                        # Do not proceed to more dates/pages after an access failure.
                        break
                success += 1
                warnings.extend(payload["warnings"])
                if payload["warnings"]: incomplete = True
                for raw in payload["records"]:
                    if raw.get("source") != SOURCE or raw.get("is_mock") is not False:
                        incomplete = True; warnings.append("キャッシュのsource/is_mock不整合を除外"); continue
                    if codes and raw["code"] not in codes: continue
                    event = normalize_event(raw, source=SOURCE)
                    key = f"{day}:{event.code}:{raw['disclosure_id']}"
                    events[key] = event
                    # Stable per-disclosure key, one value even across pages/runs.
                    event_path = self.cache / (sha256(key.encode()).hexdigest() + ".json")
                    record = {"key": key, "record": raw}
                    if not event_path.exists() or json.loads(event_path.read_text(encoding="utf-8")) != record:
                        atomic_json(event_path, record)
                pages.extend(p for p in payload["pages"] if p not in seen and p not in pages)
            if incomplete: break
        status = "partial" if incomplete and success else "error" if incomplete and failures else "unavailable" if incomplete else "ok"
        return ProviderResult(status, tuple(events.values()), cache_hits=hits, request_count=requests,
                              success_count=success, failure_count=failures, warnings=tuple(dict.fromkeys(warnings)), **base)


class MockTDnetProvider(TDnetProvider):
    def __init__(self, events=None, *, as_of="2026-09-06T12:00:00+09:00"):
        self.adapter = MockTDnetAdapter(events, as_of=as_of)

    def fetch_events(self, *, dates, codes=None):
        days = tuple(str(d) for d in dates)
        fetched = timestamp().isoformat()
        events = tuple(replace(e, is_mock=True, source="mock", fetched_at=fetched) for e in self.adapter.events
                       if str(e.published_at.astimezone(JST).date()) in days and (codes is None or e.code in codes))
        return ProviderResult("ok", events, "mock", True, fetched, days, tuple(codes or ()), success_count=1)


def create_provider(name="free_public", **kwargs):
    providers = {"free_public": FreePublicTDnetProvider, "mock": MockTDnetProvider}
    if name not in providers: raise ValueError("provider not implemented; paid providers are future extensions")
    return providers[name](**kwargs)


def production_results(result, codes):
    """Only complete, real results enter production. Partial coverage is unknown."""
    valid = result.status == "ok" and not result.is_mock and result.source != "mock"
    contaminated = any(e.is_mock or e.source == "mock" for e in result.events)
    status = "ok" if valid and not contaminated else "unavailable"
    return {code: TDnetResult(status if not result.codes or code in result.codes else "unavailable",
                              tuple(e for e in result.events if e.code == code) if status == "ok" and (not result.codes or code in result.codes) else (),
                              provider_name=result.source, fetched_at=result.fetched_at) for code in codes}
