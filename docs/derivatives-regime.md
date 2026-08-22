# Derivatives Regime Shadow Layer

The derivatives layer is an observation and research layer. It is not a
trading strategy and it does not change `crypto.signal.v1`, Paper positions,
or Live execution.

## Current implementation

`crypto_simulator.derivatives` normalizes public perpetual snapshots from:

- Hyperliquid `metaAndAssetCtxs`
- MEXC contract ticker plus current funding-rate endpoint
- Bybit V5 linear perpetual tickers
- OKX SWAP ticker, mark price, open interest, and funding endpoints

The normalized `DerivativesObservation` records the venue, symbol, fetch
(`observed_at`), exchange and publication timestamps, mark/index prices, open
interest, USD open interest when supplied, funding rate, funding interval,
funding status, next funding time, 24-hour volume, source, and data-quality
status. Current predicted funding is never relabelled as finalized or paid.
Funding is converted to a per-hour value only when the provider supplies a
usable funding interval.

`build_derivatives_features` uses only observations at or before the requested
`as_of` time. It calculates cross-venue medians for price/OI changes and
funding, requires a configurable venue quorum, and reports missing history or
stale observations instead of filling them with guesses.

The initial coarse labels are descriptive quadrants:

- `price_up_oi_up` → `leveraged_long_expansion`
- `price_down_oi_up` → `leveraged_short_expansion`
- `price_up_oi_down` → `short_covering`
- `price_down_oi_down` → `deleveraging`

They are evidence for later research, not entry or exit instructions. A stale,
incomplete, or below-quorum result is `no_trade: true` in the Shadow report.

## CLI

Collect a current public snapshot for BTC and ETH:

```powershell
python -m crypto_simulator derivatives-shadow --output state/derivatives-shadow.json
```

To retain local history without committing it to GitHub, provide a DuckDB
path. The command upserts the new snapshots, reloads the selected symbol
history, and then evaluates the report point-in-time:

```powershell
python -m crypto_simulator derivatives-shadow \
  --database data/derivatives.duckdb \
  --output state/derivatives-shadow.json
```

DuckDB is optional; install `crypto-simulator[analysis]` when using this mode.

Select one or more venues and symbols:

```powershell
python -m crypto_simulator derivatives-shadow --venue mexc --venue hyperliquid --venue bybit --symbol BTC --output state/derivatives-shadow.json
```

For deterministic offline evaluation, pass an observation fixture. A second
fixture can contain prior observations for the 1-hour/4-hour/24-hour changes:

```powershell
python -m crypto_simulator derivatives-shadow \
  --history state/derivatives-history.json \
  --input state/derivatives-current.json \
  --as-of 2026-08-22T00:00:00Z \
  --output state/derivatives-shadow.json
```

The report is `crypto.derivatives-shadow.v1` and explicitly includes
`mode: shadow` and `canonical_strategy_changed: false`.

## Safety boundary

The current layer intentionally does not include liquidation feeds, taker
imbalance, automatic altcoin universe expansion, historical backfill, or
strategy promotion. Those require point-in-time data coverage and additional
validation. Funding intervals and exchange semantics must remain part of the
stored observation so a provider-side interval change cannot silently distort
the feature series.
