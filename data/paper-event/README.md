# Parallel event Paper snapshots

This directory contains the public, compact inputs for the experimental Paper
track. The hourly workflow generates one versioned event-permission strategy
per venue:

- `bitbank_event_long_permission_filter_v1` — Bitbank spot, JPY 10,000 start
- `hyperliquid_event_long_permission_filter_v1` — Hyperliquid perpetual, USDC 100 start
- `mexc_event_long_permission_filter_v1` — MEXC perpetual, USDT 100 start

These tracks run beside the existing Paper strategies. They are long-only,
1x maximum gross leverage, risk-on permission only, limit-entry only, and
research candidates. They are not live executors and do not place exchange
orders.

The JSON files are canonical decisions. The CSV files contain only the latest
96 one-hour bars needed by the private Paper bridge for fills, stops, targets,
and time exits. Full historical candles remain temporary inside the Action
runner; research evidence belongs in the research ledger rather than in every
hourly Git commit.
