# Multi-strategy fan-out

`config/strategies/*.json` is the declarative catalog for strategy instances.
Each strategy has an immutable `(strategy_id, strategy_version,
parameters_hash)` identity and produces a separate Paper account identity for
each venue/mode combination.

`build_feature_snapshot()` normalizes one closed OHLCV history and calculates
common features once. `run_strategy_fanout()` then evaluates every matching
Paper strategy against that immutable snapshot. Adding a strategy therefore
does not add an exchange fetch or a Cron entry.

The fan-out is intentionally a library boundary at this stage. Existing
public workflows and the current Paper bridge are unchanged until a reviewed
adapter maps a specific strategy definition to its existing signal/bracket
implementation. Missing funding/OI or stale/gapped candles produce an
explicit `no_trade` result; they are never guessed or filled in.

Example registry entries currently describe the existing parallel research
tracks:

- Bitbank spot event-permission long, effective 1x;
- Hyperliquid perpetual event-permission long, effective 1x;
- MEXC perpetual event-permission long, effective 1x;
- the existing perpetual SMA 12/24 long/short Paper comparison, capped at 5x
  but still configured for 1x effective Paper sizing by the operations layer.

The registry is a catalog, not a promotion decision. A strategy remains in
research/Paper only according to the fixed Research Protocol and promotion
gate.
