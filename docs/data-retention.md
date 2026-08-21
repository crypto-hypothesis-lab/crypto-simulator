# Data retention boundary

Keep GitHub small and reproducible:

- code, schemas, manifests, promotion reports, and a latest snapshot stay in
  GitHub;
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
