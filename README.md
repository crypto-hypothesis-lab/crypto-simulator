# Crypto Simulator

Exchange-neutral research components for crypto market data, feature calculation, and paper backtesting.

This repository is intentionally safe to publish. It contains no API keys, private strategy thresholds, live positions, or order-execution credentials.

## Current scope

- Public OHLCV adapters for Binance, Hyperliquid, bitbank, GMO Coin, and MEXC perpetuals.
- One normalized `OHLCVBar` model with UTC timestamps and decimal prices.
- A no-lookahead long-only multi-timeframe SMA backtester.
- A three-layer decision model: 1-hour execution, 4-hour trend filter, and 1-day regime filter.
- A regime-aware cross-sectional portfolio researcher for spot long-only,
  Bitbank-style margin long/short, and perpetual long/short markets.
- A limit-entry bracket researcher: daily regime, 4-hour theme/trend, and
  1-hour execution with a finite pullback limit order, ATR stop, R-multiple
  take-profit, order expiry, and a hard 30-day maximum holding period.
- A separate short-only spike-fade researcher: detect an abnormal pump,
  require rejection confirmation, then enter at the next bar open with ATR
  stops, targets, time stops, funding/credit costs, and walk-forward reporting.
- A rolling CSV collector that merges candles without duplicates.
- Backtest reports and deterministic paper-signal JSON output.
- A frozen forward-test report for the latest holdout window, including trades,
  costs, and a Buy & Hold comparison.
- Standard-library-only runtime dependencies.

Live orders, account state, private data, alert destinations, and operational policy belong in the private `crypto-operations` repository.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
python -m crypto_simulator demo
```

The first paper-trading configuration is bitbank BTC/JPY spot and long-only:

- 1-hour: SMA(20/50) creates the entry/exit decision.
- 4-hour: SMA(5/20) is the trend filter.
- 1-day: SMA(5/20) is the broad regime filter.
- Fees are 10 bps, slippage is 5 bps, spread and market impact default to 0 bps,
  and maximum holding time is 30 days.

The higher-timeframe bars are aggregated only from complete, contiguous 1-hour
candles. Until enough 4-hour and 1-day history exists, the multi-timeframe
strategy returns `hold`; this is an intentional cold-start safety behavior.

Optional integrations are kept out of the base install:

```powershell
python -m pip install -e ".[dev,analysis,exchanges]"
```

Fetch public candles without credentials:

```powershell
python -m crypto_simulator fetch --exchange hyperliquid --symbol BTC --interval 1h --hours 72 --output data/btc.csv
python -m crypto_simulator fetch --exchange bitbank --symbol btc_jpy --interval 1hour --hours 72 --output data/btc_jpy.csv
python -m crypto_simulator fetch --exchange gmo --symbol BTC --interval 1hour --hours 72 --output data/gmo_btc.csv
python -m crypto_simulator fetch --exchange binance --symbol BTCUSDT --interval 1h --days 365 --output data/binance_btcusdt_1h.csv
python -m crypto_simulator fetch --exchange mexc --symbol BTC_USDT --interval 4h --days 365 --output data/mexc/BTC_USDT_4h.csv
python -m crypto_simulator fetch-funding --exchange mexc --symbol BTC_USDT --days 365 --output data/mexc/BTC_USDT_funding.json
# Reproducible long-history fetch (UTC window):
python -m crypto_simulator fetch --exchange bitbank --symbol btc_jpy --interval 1hour --start 2025-08-15T00:00:00Z --end 2026-08-15T00:00:00Z --output data/bitbank_btc_jpy_1year.csv
```

For the recommended rolling dataset, run the following once or let the included
GitHub Actions workflow run hourly:

```powershell
python -m crypto_simulator collect --exchange bitbank --symbol btc_jpy --interval 1hour --hours 72 --output data/bitbank_btc_jpy_1hour.csv
python -m crypto_simulator backtest --input data/bitbank_btc_jpy_1hour.csv
python -m crypto_simulator research --input data/bitbank_btc_jpy_1hour.csv --output state/strategy-search.json
python -m crypto_simulator forward-test --input data/bitbank_btc_jpy_1hour.csv --holdout-days 30 --output state/forward-test.json
python -m crypto_simulator portfolio-research --market spot --input BTC=data/btc_jpy_1day.csv --input ETH=data/eth_jpy_1day.csv --input XRP=data/xrp_jpy_1day.csv --output state/spot-portfolio-search.json
python -m crypto_simulator portfolio-research --market margin --input BTC=data/btc_jpy_1day.csv --input ETH=data/eth_jpy_1day.csv --input XRP=data/xrp_jpy_1day.csv --margin-interest-bps-per-day 4 --max-gross-leverage 2 --output state/bitbank-margin-portfolio-search.json
python -m crypto_simulator spike-fade-research --market perpetual --input BTC=data/btc_4hour.csv --input ETH=data/eth_4hour.csv --input SOL=data/sol_4hour.csv --output state/hyperliquid-spike-fade-search.json
python -m crypto_simulator limit-bracket-research --market perpetual --interval 1hour --input BTC=data/btc_1hour.csv --input ETH=data/eth_1hour.csv --input SOL=data/sol_1hour.csv --input XRP=data/xrp_1hour.csv --output state/hyperliquid-limit-bracket-search.json
python -m crypto_simulator limit-bracket-signal --market perpetual --interval 1hour --profile balanced --input BTC=data/btc_1hour.csv --input ETH=data/eth_1hour.csv --input SOL=data/sol_1hour.csv --input XRP=data/xrp_1hour.csv --output state/latest-bracket-signal.json
python -m crypto_simulator signal --input data/bitbank_btc_jpy_1hour.csv --interval 1hour --output state/latest-signal.json
# For a baseline comparison only:
python -m crypto_simulator signal --single-timeframe --input data/bitbank_btc_jpy_1hour.csv --interval 1hour --output state/latest-signal.json
python -m crypto_simulator duckdb-import --input data/bitbank_btc_jpy_1hour.csv --database data/crypto-market.duckdb
```

`collect` keeps a rolling overlap so a late candle does not create a duplicate.
The hourly public collection workflow also writes `data/bitbank_btc_jpy_bracket_signal.json`, a read-only `crypto.bracket-signal.v1` snapshot for the private paper bridge. It contains no credentials; the private gate must still reject stale snapshots and re-check every candidate.\n\nThe default `signal` command ignores the currently forming candle, evaluates all
three layers, and sets `price` to the next 1-hour candle open so it matches the
backtest execution model. It requires that next candle to be present in the
dataset; a missing execution candle is a safe error, not a close-price guess.
The event also preserves `decision_price`, `execution_price`, and the individual
layer decisions for audit and reconciliation in the private operations
repository. Use `--single-timeframe` only for a baseline comparison.

`research` evaluates a small, pre-declared candidate grid. It reports total and
annualized return, maximum drawdown, Sharpe/Sortino/Calmar, exposure, turnover,
win rate, alpha/beta, and excess return over buy-and-hold. It also performs
walk-forward selection when the dataset contains enough history. A candidate
that is merely the best full-sample result is not considered validated. The
report also includes expected bars, missing candles, duplicate timestamps, and
the largest gap so a short or discontinuous dataset is visible before results
are interpreted. Spread and market impact can be stressed explicitly, for
example with `--spread-bps 10 --market-impact-bps 5`. The report marks a result
`not_validated` unless at least 60% of OOS windows beat buy-and-hold on excess
return and the median OOS excess return is positive; fewer than six qualifying
windows are still treated as low statistical power.

MEXC perpetual research uses the public contract API. The `portfolio-research`
command can run long/short candidates with a gross-exposure cap up to 5x and
MEXC funding settlements supplied via `--funding`. A 5x value is a hard ceiling,
not a recommendation to maintain 5x exposure; the ATR bracket researcher also
limits risk per trade and may use much less exposure.

The event-filtered MEXC bracket candidates are selected explicitly with
`limit-bracket-research --profile mexc-event`. For a latest Paper snapshot,
use `limit-bracket-signal --profile mexc-short` or `--profile mexc-long` and
provide Funding JSON; both profiles remain research candidates until the
cost-aware `promotion-gate` passes.

`forward-test` takes one already-frozen strategy configuration and evaluates
only the latest holdout window. Earlier candles are indicator warm-up and
cannot create reported trades. The JSON is deliberately marked
`forward_test_only`/`manual_review_required`: it is evidence for a paper-trading
decision, not an automatic promotion or live-order instruction.

`portfolio-research` accepts any number of synchronized asset series and uses a
BTC trend/breadth regime gate. Its theme proxy is deliberately data-driven:
relative momentum, short momentum, and volume acceleration. Spot candidates can
only hold positive weights or cash. Margin candidates model Bitbank credit
trading: signed long/short weights, a 2x cap, and 0.04%/day financing as a
conservative assumption. Perpetual candidates can hold signed weights and short in
risk-off regimes. Perpetual gross exposure is confidence-scaled: risk-on
exposure ranges from 1x toward a 5x cap, while risk-off short exposure is capped
at 2x; strong trends and breadth increase confidence, while realized volatility
reduces it. HyperLiquid funding observations can be passed as JSON and are
aggregated into the selected price-bar interval before charging the portfolio.
For HyperLiquid, pass a JSON symbol-to-cap map from the public `meta` response
with `--max-leverage-map`; a 3x asset is therefore capped at 3x
even when the portfolio research ceiling is 5x.

The `Research Binance BTC/USDT` workflow can be started manually from GitHub
Actions. It downloads public history, runs the fixed-candidate walk-forward
research, and uploads only the JSON report as a 30-day artifact; the raw price
history is not committed to Git.

The `Forward test Binance BTC/USDT` workflow is also manual. It accepts a
frozen execution SMA and holdout length, applies explicit spread and market
impact assumptions, and uploads only the forward-test JSON artifact. It does
not select or promote a strategy automatically.

The `Research bitbank liquid portfolio` workflow selects current JPY pairs by
24-hour quote volume and bid/ask spread, removes pairs without enough daily
history, then runs both spot and Bitbank-margin variants over that broader
universe. The `Research HyperLiquid liquid perpetual portfolio` workflow selects
current perpetuals by notional volume, removes symbols without enough daily
history, fetches funding for each remaining symbol, and applies the current
per-asset leverage caps. Both workflows are manual, cost-aware, report-only
research and upload the selection manifest with the result. The selection is a
current-liquidity snapshot, not point-in-time historical constituents, so its
result is exploratory and must not be promoted to live trading automatically.

The `Research MEXC liquid crypto perpetuals` workflow follows the same pattern
for MEXC, but also reads MEXC contract classifications to exclude stock, ETF,
and commodity perpetuals. It selects candidates by 24-hour quote turnover and
bid/ask spread, then audits historical quote turnover: gradual increases are
tagged `rising`, isolated volume jumps are tagged `surging` and receive a
smaller size multiplier, and low-base-liquidity spikes are rejected. The
default maximum is 12 symbols including BTC, with a 6,000 one-hour-bar history
floor. This remains a current-constituent research snapshot rather than a
point-in-time universe for live promotion.

The same MEXC workflow also runs weekly on Mondays at 02:15 UTC. It writes a
small `crypto.research-report.v1` decision artifact and sends the result to
the private notifier when `NOTIFY_ADMIN_TOKEN` is configured as a GitHub
Actions secret. A full-sample winner alone produces `hold`; only a complete
dataset with the required walk-forward candidate status produces
`paper_start`. In that case the workflow generates the latest closed-candle
limit-bracket snapshot and forwards it to the paper-only control plane. The
workflow never submits an exchange order, and repeated report IDs are
deduplicated by the notifier.

`spike-fade-research` is intentionally a separate hypothesis from momentum:
it detects a large return/ATR and volume excursion, waits for a rejection of
the pump range, and then shorts at the next common bar open. It uses a
stop-first rule when a stop and target are both touched in one bar, a maximum
holding period, and a small fixed risk budget per trade. The default research
interval is 4-hour because daily candles are too coarse for this event. A
positive full-sample return is not sufficient; the report also requires
positive out-of-sample return and excess return across the walk-forward windows
before it can be considered a candidate for forward testing.

`limit-bracket-research` models a resting parent limit order rather than a
next-bar market entry. The signal is formed only after a closed execution
candle breaks out in the direction supported by the 4-hour trend and 1-day
regime. A long order is placed below the signal close and a short order above
it; there is no market-order fallback. The order can fill at its limit or at a
better opening-gap price, then receives a protective ATR stop trigger and an
R-multiple take-profit limit. An unfilled order expires, a regime reversal
cancels it, and a bar that touches both exit levels is scored as a stop first.
The report includes limit fill rate, cancellations, stop/target/time-stop
counts, and average holding days so an attractive return cannot hide poor
execution.

The default bracket candidates use 7, 14, and 30-day holding caps. The
30-day candidate is a hard ceiling, not a target. Perpetual research should
pass funding JSON and a point-in-time symbol leverage map. The global research
ceiling may be 5x, but each asset cap is applied before sizing.

`limit-bracket-signal` writes the latest closed-candle decision as
`crypto.bracket-signal.v1`. It always writes a snapshot, including explicit
`no_trade_reason` during warm-up, neutral regimes, risk-off spot markets, and
periods without a valid retest. Actionable candidates contain a stable signal
ID, entry limit, protective stop, take-profit limit, expiry, risk budget, and
the evidence used by the daily/4-hour/1-hour layers. This JSON is intended to
be the only public-to-private handoff: the future `crypto-operations` adapter
can ingest it,
deduplicate by `idempotency_key`, append it to the audit ledger, then fan out
the same decision to Discord and the member dashboard without storing secrets
in this public repository.

The private `crypto-operations` repository now contains the v1 paper adapter,
strict risk re-validation, persistent state, a hash-chain ledger, and the
authenticated notifier bridge. The adapter is paper-only and still requires an
external scheduler to provide the latest decision/bar files and run
`bracket-bridge`; merging the repositories does not create that scheduler or
populate Cloudflare/GitHub secrets. Until that runtime wiring is configured,
this command remains a research snapshot and must not be treated as a
live-order instruction.

The regular `signal` command emits `crypto.signal.v1`. Its
`candle_close_at`, `strategy_version`, `signal_key`, and `event_id` identify a
single closed-candle decision deterministically. Private consumers should use
that identity for retry-safe processing; the forming candle is never used as
the decision candle.

The handoff rules and machine-readable contract are documented in
[`docs/operations-integration.md`](docs/operations-integration.md) and
[`docs/crypto-bracket-signal-v1.schema.json`](docs/crypto-bracket-signal-v1.schema.json).

CCXT is available as a public-data-only adapter for supported venues. It does
not accept API keys and exposes no order or withdrawal methods:

```powershell
python -m crypto_simulator fetch --exchange ccxt --ccxt-id bitbank --symbol BTC/JPY --interval 1h --hours 72 --output data/ccxt_btc_jpy.csv
```

DuckDB is local storage for research queries; it is not a remote service and is
ignored by Git. The GitHub Actions workflow uses pinned action commit SHAs and
only requests `contents: write` because it commits the public candle dataset.

## Exchange notes

- Hyperliquid returns its most recent candle snapshot through the public `info` endpoint.
- HyperLiquid perpetual funding is fetched from the public `fundingHistory` endpoint and is charged hourly before aggregation to the research interval.
- Binance exposes paginated public klines, so the adapter pages by open time and deduplicates by timestamp.
- bitbank and GMO Coin expose date-partitioned public candlestick endpoints, so the adapters request each required UTC date and deduplicate bars.
- Providers are not interchangeable price series. The normalized record retains the source exchange and symbol so cross-venue studies cannot silently mix them.

## Research boundary

The backtester executes a signal formed at the close of bar `t` at the open of
bar `t+1`. Fees, slippage, half-spread, and market impact are explicit inputs.
This is a conservative research model, not an execution simulator.

This software is a research tool, not investment advice or an order-execution system.
