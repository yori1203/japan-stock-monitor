# V3 EDINET Full Validation recovery

Recovery baseline: remote `b1f2f87` (local prior checkout `994f9bb` had the same code).
No tracked uncommitted changes were present. Only `.cache/` and `.test-deps/` were untracked.
The existing report measured 3,707 universe entries, 400 preselected, 50 Yahoo
financial results, and 10 successful EDINET results. Prior Actions run
33967264794 validated 15 symbols (14 EDINET successes) and saved cache
`Linux-v3-edinet-2`. Neither the prior checkout nor main is modified by this recovery.

`v3_edinet_50_input.json` freezes the **existing 50 financial candidates**, not a
new universe scan. It was reconstructed from `v3-final-measure/financials.json`,
`v3-final-measure/preselection.json`, and `v3-universe-cache.json` using the
unchanged scoring functions and the original measurement timestamp. Damaged
JPX display text is replaced with official EDINET mapping metadata in output.
To rebuild only when recovering those original local caches, run
`python -m validation.prepare_recovered_input`; normal validation does not rebuild input.

```sh
python -m pytest -q
python v3_edinet_full_validation.py --batch-size 10 --max-scan-days 40 --max-seconds 240 --operation-timeout 45
# Exit 2 means incomplete: repeat the SAME command to resume.
# Exit 0 means all 50 have a terminal outcome:
python v3_edinet_full_validation.py --render
```

The API key comes only from `EDINET_API_KEY` (the Actions repository secret).
The existing workflow is dispatched on `feature/v3-discovery-engine`; it is
guarded against running this full validation job on main. It restores the old
EDINET cache, then saves the new checkpoint even on failure. No Yahoo requests
are issued by this runner. Matching document IDs reuse previously extracted data
without TTL expiry because this is a frozen historical validation, not a refresh.

Each external operation has a disposable subprocess and hard timeout; a timeout
kills and waits for that subprocess. Requests also have a 12-second socket
timeout and at most one retry. Each chunk has a total wall-clock budget.
Each completed search day and symbol is atomically checkpointed. Failed search
dates are retried, never skipped. Completed symbols are not fetched again.
Input fingerprints reject mixing checkpoints from different candidate sets.
Never run two local processes against the same checkpoint; Actions concurrency
serializes workflow runs. To resume elsewhere, download the artifact checkpoint
and place it at `.cache/v3-edinet-validation/full-checkpoint.json`.

`ok`, `no_recent_filing`, and `unmapped` are distinct terminal outcomes; only `ok`
counts as acquisition success. Transport/timeout/empty-extraction errors remain
pending and prevent final ranking generation. Absence is established only after
the entire frozen 190-day interval was searched successfully.
