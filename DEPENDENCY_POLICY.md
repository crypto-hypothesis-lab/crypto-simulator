# Dependency policy

The base package uses only the Python standard library. Optional integrations
are deliberately isolated:

- `analysis` adds DuckDB for local candle and research-result storage.
- `exchanges` adds CCXT through a public-OHLCV-only adapter.

The CCXT adapter does not accept API credentials and does not expose order,
transfer, or withdrawal methods. Exchange-specific direct adapters remain in
the repository so the public data boundary stays auditable.

GitHub Actions are pinned to full commit SHAs. Dependency upgrades should be
reviewed with the upstream license, release notes, and a clean test run before
being merged. Never add API keys, private account data, or live-execution code
to this public repository.
