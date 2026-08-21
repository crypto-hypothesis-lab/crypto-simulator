# Strategy lifecycle and lineage

The simulator is the only component that owns strategy logic and emits the
canonical `crypto.signal.v1` and `crypto.bracket-signal.v1` contracts. Every
decision must carry `signal_source=crypto-simulator`, `strategy_id`,
`strategy_version`, `signal_key`, and `event_id`.

## Promotion stages

Candidates move through the following explicit stages:

`Backtest -> Walk Forward -> Cost Stress -> Forward Test -> Paper -> Shadow Live -> Small Live -> Production`

The `promotion-gate` command records the current stage and next stage. It also
reports fill rate, expectancy, average win/loss, Profit Factor, maximum
drawdown, average holding time, and slippage when those fields exist. Optional
machine thresholds can be enabled with `--minimum-profit-factor`,
`--minimum-expectancy`, and `--maximum-drawdown`.

Passing a gate never enables live execution. Operations remains paper-only and
must perform its own stale-data, leverage, duplicate, and strategy-lineage
checks.

## Reproduction check

`paper-compare` accepts three JSON objects with the same metadata and an
`outcomes` array. It compares Backtest, Forward Test, and Paper metrics. A
strategy/version mismatch is rejected; a materially worse Paper result yields
`warning` and `recommendation=hold_or_demote`.

## Storage boundary

GitHub remains the source for code, schemas, reports, and small reproducible
snapshots. High-volume OHLCV and long-lived ledgers should move to DuckDB/R2
or another append-only store when repository snapshots exceed the retention
thresholds defined in `data-retention.md`. This change is intentionally not an
automatic migration.
