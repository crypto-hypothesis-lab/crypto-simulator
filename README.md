# Crypto Simulator

Exchange-neutral research components for crypto market data, feature calculation, and paper backtesting.

This repository is intentionally safe to publish. It contains no API keys, private strategy thresholds, live positions, or order-execution credentials.

## Current scope

- Public OHLCV adapters for Hyperliquid, bitbank, and GMO Coin.
- One normalized `OHLCVBar` model with UTC timestamps and decimal prices.
- A no-lookahead long-only SMA crossover backtester.
- A rolling CSV collector that merges candles without duplicates.
- Backtest reports and deterministic paper-signal JSON output.
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

The first paper-trading configuration is bitbank BTC/JPY spot, 1-hour candles,
long-only SMA(20/50), 10 bps fees, and 5 bps slippage.

Optional integrations are kept out of the base install:

```powershell
python -m pip install -e ".[dev,analysis,exchanges]"
```

Fetch public candles without credentials:

```powershell
python -m crypto_simulator fetch --exchange hyperliquid --symbol BTC --interval 1h --hours 72 --output data/btc.csv
python -m crypto_simulator fetch --exchange bitbank --symbol btc_jpy --interval 1hour --hours 72 --output data/btc_jpy.csv
python -m crypto_simulator fetch --exchange gmo --symbol BTC --interval 1hour --hours 72 --output data/gmo_btc.csv
```

For the recommended rolling dataset, run the following once or let the included
GitHub Actions workflow run hourly:

```powershell
python -m crypto_simulator collect --exchange bitbank --symbol btc_jpy --interval 1hour --hours 72 --output data/bitbank_btc_jpy_1hour.csv
python -m crypto_simulator backtest --input data/bitbank_btc_jpy_1hour.csv
python -m crypto_simulator signal --input data/bitbank_btc_jpy_1hour.csv --interval 1hour --output state/latest-signal.json
python -m crypto_simulator duckdb-import --input data/bitbank_btc_jpy_1hour.csv --database data/crypto-market.duckdb
```

`collect` keeps a rolling overlap so a late candle does not create a duplicate.
`signal` ignores the currently forming candle and writes an event that can be
passed to the private operations repository.

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
- bitbank and GMO Coin expose date-partitioned public candlestick endpoints, so the adapters request each required UTC date and deduplicate bars.
- Providers are not interchangeable price series. The normalized record retains the source exchange and symbol so cross-venue studies cannot silently mix them.

## Research boundary

The backtester executes a signal formed at the close of bar `t` at the open of bar `t+1`. Fees and slippage are explicit inputs. This is a conservative baseline, not an execution simulator.

This software is a research tool, not investment advice or an order-execution system.
