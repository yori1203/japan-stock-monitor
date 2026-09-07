# V3 integrated pipeline

Final validation on 2026-09-07: **196 passed in 6.91s**; cached measurement
**1.406s**, zero additional acquisitions, 50 final candidates.

Production CLI: `python v3_pipeline.py --mode cached`

- `cached`: reuse saved JPX/price caches and the completed 50-stock financial/
  EDINET snapshot. No network acquisition. Cached quote scoring is inexpensive
  local computation; previously completed acquisition is not repeated.
  After a completed full financial stage, cached/quick prefer its newer saved
  candidate set. EDINET outcomes acquired by full are also retained across runs.
- `quick`: skip universe/preselection acquisition and filtering, use saved 50
  candidates, then crosscheck/rank/report. No network. Optional local quote
  snapshots provide price freshness and portfolio reference prices.
- `full`: reuse valid caches, refresh missing/stale universe, quotes and financials,
  and crosscheck up to the top 50 financial candidates using EDINET. Financial
  candidates outside the EDINET limit remain explicitly unavailable. A missing
  EDINET key only skips missing EDINET acquisition; valid cached EDINET still works.

TDnet has no permission configured in any mode. The FreePublicTDnetProvider
permission gate returns unavailable, and production_results plus the financial
ranking guard excludes mock/partial/failed results. No public TDnet requests or
paid API access are added. Unavailable TDnet scores are None, never zero.

## Checkpoints and bounded work

Stages: universe, preselection, financial, edinet, tdnet, ranking, report.
Each stores status, started_at, completed_at, input_count, success_count,
failure_count and cache_hits in `.cache/v3-pipeline/<mode>/checkpoint.json`.
Outputs are atomic JSON stage artifacts with content hashes. Input fingerprints
cover frozen financial inputs, EDINET validation results, configuration and mode.
Corrupt artifacts/changed input fail closed instead of silently repeating work.

Reinvoke the same command after interruption. Completed stages are not rerun;
missing final reports can be rebuilt independently. A completed invocation is
idempotent. Use `--new-run` for a new evaluation/updated full run. This refreshes
stage progress and the comparison baseline while retaining acquisition caches.
The evaluation date is frozen for each run, including overnight resumes.

Full mode defaults to 180 seconds, 20 external worker operations, 30 seconds per
worker. Configure with --max-seconds, --max-operations, --operation-timeout.
Network operations run in disposable subprocesses and cannot leave timed-out
worker threads blocking the parent. Exit 2 means budget exhausted with progress
saved; repeat the command. Exit 1 means a failed stage; resolve its cause and
resume. Exit 0 means complete (including explicit unavailable stages).

Quotes save after batches of 50; financials save after each stock. EDINET saves
after every search date and document. Latest filings are searched newest-first
over a fixed 190-day interval. Existing daily lists are reused. no_recent_filing
is terminal for that reference interval and is not an API failure. The original
50-symbol EDINET validation checkpoint and results are not reset or rewritten.
A cache-directory lock prevents concurrent pipelines. Inspect stale locks after
an abnormal process termination; the program never forcibly removes another run.

## Freshness and scope

Full refresh thresholds: JPX metadata 7 days, quotes 3 calendar days, financial
acquisition 7 days, saved EDINET outcome 7 days. Reporting also warns for fiscal
periods older than 180 days. These are transparent initial thresholds, not a
trading-calendar or publication-calendar model. Cached/quick modes warn and keep
old data rather than acquiring replacements. Acquisition timestamps and fiscal
period end dates are separately displayed; an updated download does not make an
old accounting period recent.

Recovered local cache: 4,441 JPX records including non-common products; 3,707
eligible equity snapshots; primary Top400; 50 saved financial candidates.
Financial cached success50 out of the historical preselection400 is a deliberate
saved-data limit, not 350 API failures. JPX metadata and snapshot cache hits count
records; financial/EDINET hits count symbols (including one cached no-filing
outcome). Do not interpret their sum as a number of HTTP requests.

Fresh checkouts without `.cache/recovery` can still replay the committed 50-stock
snapshot. Universe/preselection then show skipped/unavailable and historical
reference counts, rather than claiming a fresh full-universe acquisition. Price
and JPX freshness are unknown in that case.

## Rankings, portfolio and reports

Generated: v3_report.md, v3_final_candidates_report.md, v3_final_ranking.json,
v3_pipeline_run.json, v3_pipeline_summary.json. Reports include Top20, <=50,000
yen Top10, 5,000–15,000 yen candidates, category A, rise/fall/new entrants and
important flags. The previous ranking is snapshotted once at run start, so
resuming report generation does not compare a ranking against itself.

`config.json` is read only for portfolio holdings. Portfolio review uses available
candidate scores and saved quotes in a separate report section. Missing financial
scores or cost basis are explicit and are not fabricated. `portfolio_review`
accepts additional scored holdings in future integrations, even when they are not
discovery candidates. It exposes hold/add/profit-warning/loss-warning assessments;
these are review heuristics, not orders. Cost-based checks need average_cost or
purchase_price. The existing configuration is never modified automatically.

The old injectable run_final_pipeline API remains for compatibility; v3_pipeline.py
is the production CLI with TDnet gating and resume semantics.

## Validation and workflow

The full existing test suite plus pipeline tests passes (196 cases). Coverage
includes stage metadata, resume without upstream reruns, cached/quick networking
prohibition, EDINET49+no-filing1, mock exclusion, reports/freshness, portfolio
assessments and a synthetic full run paused after universe acquisition.
Synthetic full fixtures do not depend on private local recovery caches.

Local cached measurement reused all saved data and finished in about 1.4 seconds,
with zero new Yahoo/EDINET/TDnet acquisitions. All 50 ranks are unchanged from
the previous production ranking. The four configured holdings are outside the
saved financial50, so their financial assessment is deferred; cached prices are
shown. No +/- portfolio trade action is inferred from missing data.

`.github/workflows/v3-pipeline.yml` provides workflow_dispatch only, guarded to
feature/v3-discovery-engine, without schedules or repository write permissions.
It saves V3 caches/artifacts even on failure, and accepts exit2 as a resumable
chunk. On a first clean runner, cached/quick can use the committed 50-stock inputs
without recovery caches. Full updates are explicit manual work. No existing V2
workflow is changed and no merge into main is performed.

Remaining operational work: obtain TDnet use permission or an authorized source;
populate missing holdings financials/cost basis; calibrate freshness and portfolio
heuristics; and perform a separately authorized minimal full refresh when desired.
None of these is represented as completed live validation in this cached run.
