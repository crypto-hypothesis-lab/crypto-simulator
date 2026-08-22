# Research ledger and market-structure studies

Research failures are evidence. They must not disappear when a new parameter
set, universe, or strategy version replaces them.

## Storage split

- `data/research-ledger.duckdb` stores the complete original JSON artifacts,
  normalized strategy metrics, and each Walk Forward outcome. It is local or
  artifact storage and is not committed to Git.
- `research/evidence/known-research-history.json` is the compact, reviewable
  evidence catalog committed with the code. It contains metrics and preserves
  positive, negative/flat, no-trade, and unmeasured states separately.
- `research/evidence/failure-reasons.json` is the machine-readable catalog of
  genuinely failed performance tests: negative OOS/full-sample return,
  non-positive expectancy, or weak Profit Factor after costs. No-trade,
  insufficient-event, and data-blocked cases remain separate diagnostics in
  the main history and are not labelled as performance failures.
- `research/evidence/failure-analysis.md` is the human-readable review of the
  major failed hypotheses, including what to change in the next experiment.
- OHLCV and high-frequency derivatives history remain outside Git. Their
  dataset period, symbols, methodology, and strategy lineage are retained in
  every research run.

Import an existing result, including a failed result:

```console
python -m crypto_simulator research-record \
  --input state/mexc-event-research.json \
  --database data/research-ledger.duckdb \
  --experiment-id mexc-event-v1 \
  --conclusion rejected \
  --tag failure
```

The `research`, `forward-test`, `portfolio-research`, `spike-fade-research`,
`limit-bracket-research`, and plain `backtest` commands accept
`--research-database`. Re-importing identical JSON updates the same
content-addressed run rather than duplicating it.

Export a compact catalog for code review and the next experiment:

```console
python -m crypto_simulator research-history \
  --database data/research-ledger.duckdb \
  --output research/evidence/known-research-history.json \
  --failure-output research/evidence/failure-reasons.json
```

The performance-failure catalog is derived from the original artifact at ledger
import time. Existing ledgers are backfilled when opened, so adding a new
diagnostic field does not discard older failed experiments. Explicit
experiment-specific diagnostics may also be supplied as a top-level
`failure_reasons` list; the ledger classifies each item as
`negative_performance`, `weak_performance`, `not_measured`, `data_blocked`, or
`validation_blocked`.

## New market-structure research lines

The initial event definitions are intentionally frozen and cannot create
Paper or Live orders:

1. `mexc_long_liq_exhaustion_reclaim_v1`: price decline, OI contraction,
   abnormal volume, then a closed-candle reclaim. Until reliable liquidation
   history exists this is labelled `liquidation_proxy`, never an observed
   liquidation.
2. `mexc_crowded_long_failure_short_v1`: price/OI expansion and high causal
   Funding percentile, followed by failed continuation and actual OI unwind.
3. `mexc_oi_compression_breakout_v1`: low-range compression, neutral Funding,
   OI accumulation, and a volume-confirmed break.

Run the event study from accumulated derivatives snapshots:

```console
python -m crypto_simulator market-structure-study \
  --input BTC=data/mexc/BTC_USDT_1h.csv \
  --input ETH=data/mexc/ETH_USDT_1h.csv \
  --derivatives-database data/derivatives.duckdb \
  --research-database data/research-ledger.duckdb \
  --output state/market-structure-study.json
```

The report measures 1/4/8/12-hour direction-adjusted forward returns and
12-hour MAE/MFE. Fewer than 50 events is `insufficient_events`, not a strategy
failure and not evidence for promotion. Missing or degraded derivatives data
is counted as blocked and never guessed.

The workflow now archives each dated universe manifest. Until enough daily
manifests have accumulated, historical studies still identify their universe
as `caller_supplied_snapshot`; they must not claim a complete historical
Point-in-Time universe or pass promotion on that basis.

## Walk Forward selection

Limit-bracket research now requires at least 20 training round trips, positive
post-cost expectancy and robust score, and Profit Factor of at least 1.15
before a candidate may be selected for an OOS window. If none qualifies, the
result is explicitly `NO_STRATEGY / NO_TRADE`. Flat/no-trade OOS windows are
reported separately from losing windows.

These are research qualification floors, not Paper promotion criteria. The
stricter Research Protocol and promotion gate still apply afterward.

## Current research conclusion

No strategy currently passes the complete Paper promotion path. The most
promising next hypothesis is an OI/Funding-aware event-driven pullback/reclaim
strategy: use a point-in-time liquid universe, require a price drawdown with OI
contraction and abnormal volume, then wait for a closed-candle reclaim and a
resting limit entry. Treat the daily regime as a permission filter only. A
short setup requires a separate observed crowding-and-OI-unwind event; a
generic bearish regime is not enough.

This is a research candidate, not a Paper or Live instruction. It must first
pass the same fixed Walk Forward, cost-stress, and forward-test gates as every
other strategy.

## Fill and cost evidence

Limit-bracket research can record separate maker and taker fees plus explicit
resting-limit adverse selection and stop-gap penalties. Maker/taker values are
research assumptions, not claims about an account's current fee tier.

```console
python -m crypto_simulator limit-bracket-research ... \
  --maker-fee-bps 2 --taker-fee-bps 5 \
  --adverse-selection-bps 2 --stop-gap-penalty-bps 10
```

Resting entries and take-profit limits pay the maker assumption. Protective
stops and time exits pay the taker assumption and configured execution cost;
a gap through the stop adds the stop-gap penalty. Reports declare 1x, 1.5x,
and 2x cost-stress multipliers for subsequent ablation. Entry/exit changes are
still tested separately under Research Protocol v1.
