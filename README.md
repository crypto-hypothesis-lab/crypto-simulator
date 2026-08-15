# Crypto Simulator

Exchange-neutral research components for crypto market data, feature calculation, and paper backtesting.

This repository is intentionally safe to publish. It contains no API keys, private strategy thresholds, live positions, or order-execution credentials.

## Current scope

- Public OHLCV adapters for Hyperliquid, bitbank, and GMO Coin.
- One normalized `OHLCVBar` model with UTC timestamps and decimal prices.
- A no-lookahead long-only SMA crossover backtester.
- A small CLI for public data collection and synthetic demonstrations.
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

Fetch public candles without credentials:

```powershell
python -m crypto_simulator fetch --exchange hyperliquid --symbol BTC --interval 1h --hours 72 --output data/btc.csv
python -m crypto_simulator fetch --exchange bitbank --symbol btc_jpy --interval 1hour --hours 72 --output data/btc_jpy.csv
python -m crypto_simulator fetch --exchange gmo --symbol BTC --interval 1hour --hours 72 --output data/gmo_btc.csv
```

## Exchange notes

- Hyperliquid returns its most recent candle snapshot through the public `info` endpoint.
- bitbank and GMO Coin expose date-partitioned public candlestick endpoints, so the adapters request each required UTC date and deduplicate bars.
- Providers are not interchangeable price series. The normalized record retains the source exchange and symbol so cross-venue studies cannot silently mix them.

## Research boundary

The backtester executes a signal formed at the close of bar `t` at the open of bar `t+1`. Fees and slippage are explicit inputs. This is a conservative baseline, not an execution simulator.

This software is a research tool, not investment advice or an order-execution system.
