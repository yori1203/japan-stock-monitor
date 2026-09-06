# V3 TDnet event layer

Validation on 2026-09-06: `pytest -q` **161 passed in 1.82s** (original 96 plus
65 added cases). The old enum-count assertion is updated from 13 to 15 for the
two requested new types. Offline replay was tested with socket connections
disabled; saved Yahoo50/EDINET49 inputs were reused without fetching.

This layer performs no paid API calls. JPXTDnetAdapter and JQuantsTDnetAdapter
are injection boundaries, not completed production API clients. A credential
without an injected loader returns `unavailable`. Implement contractual access,
transport timeouts, pagination/checkpoints and schema mapping in a future provider.
Loader failures return `error`, not a successful empty disclosure list.

## Common input and compatibility

`normalize_event` accepts code, company_name, published_at (ISO8601), title,
category, source, raw_reference, fiscal_period, optional event_type and numeric
fields. Naive dates are interpreted in JST; invalid dates fail normalization.
All numeric forecast pairs must be for the **same fiscal period, scope, unit and
accounting definition**. Provider mappers are responsible for that alignment.
The raw_reference preserves the source ID/location for future audit and parsing.

Example:

```python
from tdnet_adapter import JPXTDnetAdapter

provider = JPXTDnetAdapter(loader=lambda code: [{
    "code": code, "published_at": "2026-09-06T12:00:00+09:00",
    "title": "通期業績予想修正", "fiscal_period": "2027-03",
    "forecasts": {"operating_profit": {"old": 100, "new": 180}},
    "raw_reference": "example-disclosure-id",
}])
# Inject a loader only after integrating a contracted provider.
result = provider.fetch_disclosures("1000")
```

The original TDnetEvent seven positional arguments, datetime keyword/property,
TDnetResult status/events/reason, and all four fetch methods are retained.
The enum expands from 13 to 15 values (restructuring and delisting_related).
`published_at` is the serialized canonical timestamp.

## Classification and impact (0 negative, 50 neutral, 100 positive)

Supported: earnings, upward_revision, downward_revision, dividend_increase,
dividend_decrease, share_issuance, warrant, stock_option, share_buyback,
capital_alliance, business_alliance, m_and_a, restructuring,
delisting_related, other_material.

NFKC normalization handles Japanese/full-width variants. Ambiguous revision
headlines stay other_material unless paired numbers establish direction.
Explicit provider event_type is accepted; forecast numbers determine revision
direction even if the title disagrees. Equal paired values are neutral.
Each disclosure has one primary type; multi-topic provider records can be split
into distinct primary events before normalization. No PDF/NLP extraction is included.

Forecast metrics are selected in order: operating_profit (operating_income),
ordinary_profit, net_profit (net_income), EPS, revenue (sales), including Japanese
aliases. This prioritizes operating profit rather than averaging conflicting
metrics. Alternatively pass metric/current_value/previous_value directly.
`change_rate=(new-old)/abs(old)`. For nonzero old values impact is
`50 +/- min(45, 15 + abs(change_rate)*50)`. Without numbers the shift is 20;
大幅 raises it to at least 35. Profit moving from <=0 to >0 scores 95;
>=0 to <0 scores 5. Zero denominators yield change_rate=None. Revenue is not
treated as a profit turnaround. Numeric evidence confidence=0.95; recognized
headline confidence=0.75; unknown confidence=0.35.

Base impacts: dividend increase/buyback 75, dividend cut 25, capital alliance 65,
business alliance 60, M&A 55, restructuring 15, delisting 0, earnings/unknown 50.
Buyback >=5% (buyback_rate is a fraction) or a large-buyback headline scores 90.
These are configurable-layer heuristics for screening, not calibrated return forecasts.

## Dilution

`new_shares/existing_shares` requires finite, nonnegative new shares and positive
existing shares. <=3% minor=40, >3% and <10% caution=30, >=10% and <20%
high=15, >=20% severe=5; unknown size=30. >=10% sets major_dilution.
Warrants, CB/convertibles, issuance and stock options are recognized. MS warrants
score 5 regardless of a missing size. `use_of_proceeds` and `growth_investment`
are retained but do not automatically waive dilution risk. Store shares on a
fully diluted basis in the provider mapper when appropriate.

## Aggregation and final ranking

JST calendar-day decay: 0–3=1, 4–7=.8, 8–14=.6, 15–30=.4, >30=.2.
Future events are excluded; identical normalized disclosures are deduplicated.
For each event delta=(impact-50)*decay*confidence. Strongest absolute delta wins
(negative wins ties); add 25% of other deltas capped to the strongest magnitude.
Clamp the result to 0–100. Positive/risk flags and up to three important event
titles are exposed in final candidates. Long-lived events retain 20% weight;
providers should define their retrieval horizon explicitly.

EventScoringConfig controls thresholds and overrides: impact>=85 permits up to
+8; impact<=15, major dilution or MS warrant permits up to -15. Both overrides
decay and scale by confidence. If both directions exist, the strongest negative
override wins. Overrides are capped per stock, not summed, and final score is
clamped to 0–100. They are separate from the weighted TDnet component.

FinalRankingConfig.weights defaults to preselection15 / financial35 /
crosscheck15 / data_quality10 / small_investment10 / tdnet_event10 /
risk_adjustment5. Missing components are excluded from the denominator.
TDnet unavailable/error => None, no flags or override, and explicit TDnet未照合.
Successful empty result => neutral50. This distinction can lower a high-scoring
stock's total after even a moderate positive event; compare to a neutral result
with identical coverage to isolate material impact. Existing financial risk
penalties remain separate; TDnet flags are not double-penalized there.

Pass per-code TDnetResult objects as `tdnet_results` to rank_financial_candidates.
All ranking evaluation uses generated_at as its common as-of timestamp.

## Offline validation and outputs

`python v3_tdnet_validation.py` reuses the committed Yahoo50 and EDINET49 results;
no acquisitions or EDINET validation are repeated. `--as-of` fixes evaluation
time; `--output-dir` redirects generated artifacts. Production output remains
TDnet unavailable. The new weight scheme also changes some baseline scores;
do not attribute that to real TDnet news.

- v3_final_candidates_report.md and v3_final_ranking.json: real cached financial
  data, new weights, TDnet unavailable.
- v3_tdnet_event_report.md / v3_tdnet_mock_events.json: seven clearly labeled
  hypothetical events with provider metadata and normalized fields.
- v3_tdnet_mock_ranking.json / v3_tdnet_mock_candidates_report.md: mock scenario.
- v3_tdnet_mock_comparison.md: changes versus new-weight unavailable baseline
  and versus new-weight neutral coverage.

No V2 files, workflows, signals, main, or automated trading paths are modified.
Next: choose/contract a provider, map and validate real fields, add bounded
incremental acquisition with cached disclosure IDs, and calibrate score thresholds
against event outcomes before operational use.
