# TDnet public provider completion

Validation: `pytest -q`, **185 passed in 2.05s** on 2026-09-07. All prior 161
cases remain passing, plus 24 provider cases. Tests use synthetic HTML and
injected transports/clocks; no network is required. V2/main are unchanged.

## Permission decision

The [JPX terms](https://www.jpx.co.jp/term-of-use/index.html) restrict commercial
collection without permission and high-frequency/high-load automatic access.
The [TDnet disclaimer](https://www.release.tdnet.info/inbs/js/I_MENSEKI.js)
also prohibits unauthorized reuse/reproduction. Public availability is not
treated as permission for automatic collection and storage.

`FreePublicTDnetProvider` defaults to **unavailable**, with zero requests and
no cache writes. `permission_reference` must identify actual, independently
confirmed permission covering the intended collection and cache use; it is an
operator-supplied evidence reference, not automatic legal verification. Do not
set it to a homepage URL or invent permission. There is no permission override
flag in the smoke CLI. No paid service was contracted or accessed.

Before the interrupted session, one official list page and its disclaimer
JavaScript were viewed to determine structure and conditions. That inspection
is not counted as authorized production ingestion. No further live requests
were made on resume. The local inspection files remain ignored in `.cache/`;
no source HTML or disclosure corpus is published to GitHub.

## Interfaces and normalization

`TDnetProvider` defines fetch_events(dates=..., codes=...),
fetch_events_for_codes(codes, dates=...), and fetch_events_for_date(date, codes=...).
The factory switches `free_public` / `mock`. Future JQuantsTDnetProvider or
OfficialTDnetApiProvider implementations can implement the same abstract methods;
no paid endpoint or placeholder network call is provided now.

ProviderResult carries status, events, source, is_mock, fetched_at, dates/codes,
cache hits, request/success/failure counters and warnings. The parser validates
the page date and known TDnet row classes, required code/company/time/title and
official relative document identifiers. The fifth trailing zero in a TDnet
security code is removed, including alphanumeric codes. Category is None where
the page does not supply it; event_type comes from the existing title classifier.
Financial amounts, forecast direction and dilution size are never invented.
Deleted/incomplete rows give partial coverage; unknown HTML gives error, not an
empty success. Strike-through old titles are ignored. PDFs are not downloaded.

## Access and cache

Default: serial requests, 10s interval, 10s socket timeout, one retry with
exponential backoff, at most two requests / one date / one page per invocation.
Hard settings restrict interval >=5s, timeout <=20s, retries <=2, requests <=4,
dates/pages <=2. Redirects and other hosts are not followed. Responses are capped
at 2MB. 403 persists a 24-hour cooldown; 429 persists at least one hour and honors
a longer Retry-After. Neither is retried within the run. 5xx/timeout receive only
the bounded retries. No parallel fetching, proxy, credential tricks or scheduled
polling exists. The socket timeout is not a whole-process deadline.

The public date range is 31 days per the [official guide](https://www.jpx.co.jp/listing/disclosure/01.html).
Out-of-range dates do not trigger requests; already cached historical snapshots
can still be read when use permission exists. A shared cache lock prevents
concurrent calls; a leftover lock requires operator inspection, never automatic
force removal. Access intervals/cooldowns persist across restarts.

Atomic page checkpoints record successful parsing. A later invocation can
reuse page 1 and fetch only remaining pages within its budget. Same-day cache
TTL is at least 30 minutes; historical snapshots are reused without automatic
refresh. Historical corrections after the snapshot are not automatically known.
Date+code+disclosure-ID is the stable deduplication key; per-disclosure cache
filenames are SHA256 of that key. Repeated pages/IDs count once. Cache corruption
fails safely instead of silently triggering more requests. Mock uses no public
cache. source/is_mock contamination is rejected before scoring.

## Production ranking and artifacts

`production_results(result, codes)` maps only complete `ok`, non-mock coverage
to the existing TDnetResult. Partial/error/unavailable are unscored; requested
codes outside a restricted result are also unavailable. A successful complete
empty result is neutral50, while unavailable is None and reweighted.

`rank_financial_candidates` also guards against mock by default. The existing
offline mock replay now explicitly passes `production=False`; this does not
change its hypothetical output. The lower-level scoring entry still supports
explicit status='mock' simulations, but rejects mock-marked events labeled 'ok'.
Production callers must use the production bridge/default financial entry.

`python v3_tdnet_provider_validation.py` replays saved 50-candidate results,
tests the default permission gate, and generates v3_tdnet_events_report.md and
v3_tdnet_provider_validation.json. It does not overwrite the previous production
ranking or mock reports. Current result: unavailable, events0, requests0,
cache hits0, failures0, ranking changes0. Policy unavailability is not an HTTP
failure. Positive/negative ranking effects are verified using synthetic provider
responses in tests, not misrepresented as real disclosures.

Next: obtain permission for the intended free public use or select an authorized
source; then verify a bounded real acquisition using that permission. Until then
no real-ingestion success is claimed. Add real-schema regression samples only
with redistribution permission, and validate amended/deleted disclosures before
any recurring operational use.
