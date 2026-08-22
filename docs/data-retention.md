# Data retention boundary

Keep GitHub small and reproducible:

- code, schemas, manifests, promotion reports, and a latest snapshot stay in
  GitHub;
- compact research evidence, including rejected and zero-trade experiments,
  stays in `research/evidence/known-research-history.json`;
- reasons for genuinely negative/weak performance stay in
  `research/evidence/failure-reasons.json`; unmeasured, data-blocked, and
  validation-blocked cases remain separate diagnostics in the research history;
  a readable review is in `research/evidence/failure-analysis.md`;
- complete research JSON artifacts and normalized Walk Forward outcomes stay
  in the content-addressed `data/research-ledger.duckdb` ledger;
- raw OHLCV, repeated collector output, and append-only Paper history belong in
  DuckDB/R2 or an equivalent storage layer;
- do not commit credentials, exchange private data, or Discord URLs.

Review the storage boundary when any of these becomes true:

- the working data directory exceeds 100 MB;
- one month produces more than 100 data commits;
- CI spends more than 5 minutes only restoring or diffing data;
- a single symbol history is duplicated across more than two artifacts.

Until a threshold is reached, the current small snapshots remain useful for
reproducible review. Moving storage is a separate migration with a manifest,
checksum, and restore test; it is not part of strategy promotion.

## Heavy test data

The current workflow fetches public OHLCV when it runs, keeps the compact
evidence in GitHub, and uploads the full ledger and reports as short-lived
Actions artifacts. Raw one-year OHLCV and derivatives history are intentionally
not committed. When the same history is reused often enough that Action cache
eviction or fetch time becomes material, store the raw CSV/DuckDB files on
Sakura or R2 and keep a Git-tracked manifest containing the object key,
content SHA-256, dataset period, symbols, and schema version. A future restore
must verify the checksum before a backtest is allowed to run.
