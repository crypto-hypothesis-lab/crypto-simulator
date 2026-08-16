# Crypto Simulator

Exchange-neutral research components for crypto market data, feature calculation, and paper backtesting.

This repository is intentionally safe to publish. It contains no API keys, private strategy thresholds, live positions, or order-execution credentials.

## Current scope

- Public OHLCV adapters for Binance, Hyperliquid, bitbank, and GMO Coin.
- One normalized `OHLCVBar` model with UTC timestamps and decimal prices.
- A no-lookahead long-only multi-timeframe SMA backtester.
- A three-layer decision model: 1-hour execution, 4-hour trend filter, and 1-day regime filter.
- A regime-aware cross-sectional portfolio researcher for spot long-only,
  Bitbank-style margin long/short, and perpetual long/short markets.
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
python -m crypto_simulator signal --input data/bitbank_btc_jpy_1hour.csv --interval 1hour --output state/latest-signal.json
# For a baseline comparison only:
python -m crypto_simulator signal --single-timeframe --input data/bitbank_btc_jpy_1hour.csv --interval 1hour --output state/latest-signal.json
python -m crypto_simulator duckdb-import --input data/bitbank_btc_jpy_1hour.csv --database data/crypto-market.duckdb
```

`collect` keeps a rolling overlap so a late candle does not create a duplicate.
The default `signal` command ignores the currently forming candle, evaluates all
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

`forward-test` takes one already-frozen strategy configuration and evaluates
only the latest holdout window. Earlier candles are indicator warm-up and
cannot create reported trades. The JSON is deliberately marked
`forward_test_only`/`manual_review_required`: it is evidence for a paper-trading
decision, not an automatic promotion or live-order instruction.

`portfolio-research` evaluates a small fixed universe with a BTC trend/breadth
regime gate. Its theme proxy is deliberately data-driven: relative momentum,
short momentum, and volume acceleration. Spot candidates can only hold positive
weights or cash. Margin candidates model Bitbank credit trading: signed
long/short weights, a 2x cap, and 0.04%/day financing as a conservative
assumption. Perpetual candidates can hold signed weights and short in
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

The `Research bitbank spot portfolio` workflow also runs a Bitbank margin
variant with short positions and the documented 0.04%/day credit-interest
assumption. The `Research HyperLiquid perpetual portfolio` workflow fetches
current public per-asset leverage caps before testing. Both workflows are
manual, cost-aware, report-only research, and do not store raw market data in
Git.

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
