# V3 EDINET Full Validation completion

- Validated source commit: `6743206b22e7d4f6b7351fb551bb0cca7d68c87e`
- Successful run: https://github.com/yori1203/japan-stock-monitor/actions/runs/34009901176
- Validation and ranking completed: 2026-09-06 12:51 JST
- Frozen data reference: 2026-09-05; Yahoo acquisition was not repeated.
- Tests: `pytest -q`, 96 passed in 1.29s on the successful Actions run.
- Outcomes: EDINET ok=49, no_recent_filing=1 (7803), other failures=0.
- Field comparisons: matched=149, warning=87; period mismatch symbols=13; unit corrections=5.
- Existing document extractions reused: 10; newly acquired: 39.
- 190 search days complete; checkpoint contains all 50 outcomes.
- Resumed the failed prior run's checkpoint; five bounded chunks completed the remaining work.
- Prior failure was a null submission timestamp; fixed without resetting the checkpoint.
- Artifact SHA256 verified: `729f79111f2fc7cae1156ccbe9b4febd8c8aca2d7991ca2882e1a5d5f720ed43`.
- Local checkpoint and artifact results match; all 50 codes match the frozen input and ranking.
- Changed files are confined to V3 validation and outputs; main and V2 were not changed.

## Ranking changes from b1f2f87

- EDINET successes: 10 -> 49.
- Categories: A2/B20/C23/D5 -> A3/B16/C25/D6.
- Top two unchanged: 8614 (82.57), 2317 (81.76).
- 2146 stays third; 76.60 -> 80.11, category B -> A.
- 8572: 10th -> 4th; 74.17 -> 78.04.
- 8616: 13th -> 5th; 73.33 -> 77.33.
- 2181: 4th -> 13th; 76.42 -> 73.76.
- The ranking change uses additional EDINET crosschecks with the same saved Yahoo data.

The `no_recent_filing` result means no supported XBRL filing was found in the
frozen 190-day interval; it is not an API failure. Warnings are field comparisons,
not a count of failed symbols. See the detailed validation report for each field.
