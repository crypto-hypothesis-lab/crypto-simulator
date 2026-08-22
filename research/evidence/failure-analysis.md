# Failed research review

This file is the human-readable companion to
[`failure-reasons.json`](failure-reasons.json). Here, “failed” means the
strategy produced an adverse post-cost result or failed a performance floor.
No-trade, insufficient-event, and data-blocked results are retained as
diagnostics, but are not counted as failed performance tests.

## Current conclusion

There is no strategy in the recorded results that is ready for Paper promotion.
The strongest *research candidate* is an OI/Funding-aware event-driven
pullback/reclaim strategy. It is not yet a trading strategy: it must pass the
same fixed Walk Forward, cost-stress, and forward-test gates before it can be
connected to Paper.

## What failed and why

### `mexc_event_long_pullback_atr_v1`

This is the most promising existing price-only hypothesis on full-sample
metrics, but it is not validated.

- Updated one-year run: 26 round trips, +1.03% total return, PF 2.20, maximum
  drawdown 0.36%.
- Six OOS windows: zero positive-return windows, one negative window, five
  flat/no-trade windows, and three windows without a qualified training
  candidate.
- Loss-excursion review: 10 losing trades, five with MFE below 0.5R, two with
  MFE at least 1R, and six with MAE at least 1R.

Interpretation: most losses did not first become large missed winners. Exit
tuning alone is therefore unlikely to repair the strategy. The next test must
add a different information source, especially OI and Funding, while keeping
the entry and cost assumptions frozen for comparison.

### Regime momentum portfolio

The 5x run looked strong enough to be labelled `candidate_requires_forward_test`,
but that is not evidence that 5x is safe. The 1x/2x/3x variants were not
validated consistently, and the higher-leverage runs had materially larger
drawdowns. This family remains a benchmark only; leverage is not the source of
alpha and is capped at 1x during research.

### Limit-retest / bracket strategies

The more recent standard run produced positive full-sample returns, but PF was
only about 1.02–1.04 and the robust score remained negative. The older 4-hour
run was outright negative. This indicates weak edge and sensitivity to fees,
spread, adverse selection, and stop gaps. It is not a Paper candidate.

### Spike-fade and rejection-volume short

Spike-fade had negative OOS windows and no positive-return OOS windows in the
recorded runs; several parameter sets had no observed trades. The 11-symbol
rejection-volume short had only five round trips, zero wins, and approximately
-0.62% total return. A price/volume rejection alone is not enough for a short
edge. A future short test must require observed crowding and OI unwind, not just
a bearish candle pattern.

### Regime router v2

The router produced the same candidate trades and returns as its v1 comparison
in the inspected sample. This does not prove that regime filtering is useless;
it means the router did not block the trades that determined the result. The
next experiment must log blocked candidates and their counterfactual outcomes
before changing thresholds.

### Bitbank baseline

The recent Bitbank baseline contained 201 bars and zero trades. That is
`unmeasured`, not a win and not proof that the market cannot be traded. More
history is required before judging that hypothesis.

### Market-structure event studies

The OI/Funding event definitions are research-only and currently have too few
events for promotion. Missing or degraded derivatives data is treated as
blocked. A liquidation proxy is explicitly labelled a proxy until an observed
liquidation history is available.

## Next candidate: OI/Funding-aware event reclaim

Candidate ID: `mexc_oi_funding_event_reclaim_v1`.

1. Select a point-in-time liquid perpetual universe using turnover, spread, and
   history quality.
2. Require a 6-hour price drawdown, 4-hour OI contraction, and abnormal volume
   as a liquidation/deleveraging proxy.
3. Wait for a closed-candle reclaim and place a resting pullback limit order.
4. Use the daily regime only as a permission filter; it does not automatically
   choose long or short.
5. Start long-only at 1x. Add a short branch only after a separate test shows
   high causal Funding, failed continuation, and actual OI unwind.
6. Keep the existing conservative bracket model: maker entry/TP assumption,
   taker stop/time exit, adverse selection, stop-gap penalty, and a maximum
   holding period of 30 days.

This candidate is motivated by the failure analysis, not selected because it
has already shown a profitable OOS result. The promotion state remains
`hold` until the data proves otherwise.

## How to use this in the next experiment

Before changing parameters, read the structured catalog and compare the new
run against the same strategy version, data protocol, and cost model:

```console
python -m crypto_simulator research-history \
  --database data/research-ledger.duckdb \
  --output research/evidence/known-research-history.json \
  --failure-output research/evidence/failure-reasons.json
```

Large OHLCV and derivatives files are not committed to Git. They are retained
in the DuckDB/artifact path, with Sakura or R2 suitable for long-term raw-data
storage once Action cache and fetch time become material. The GitHub evidence
files retain the dataset metadata, hashes/lineage where available, metrics,
and failure reasons needed to reproduce the research decision.
