# Investment-decision handoff

The public simulator emits one read-only decision snapshot with the schema
`crypto.bracket-signal.v1`. The private `crypto-operations` repository is the
control plane: it owns credentials, paper account state, risk approval,
deduplication, audit history, and notification delivery.

The `crypto-operations` `main` branch now contains the v1 private adapter. It
re-validates the snapshot, persists paper state and a hash-chain ledger, and
can forward only private-gate-approved decisions to the authenticated notifier.
This merge does not create a scheduler: an external runtime still has to
produce the latest decision/bar files, run `bracket-bridge`, and provide the
private notifier URL/token through its environment. Until that runtime wiring
is configured, v1 snapshots remain research output and no Discord/dashboard
update should be assumed.

The public side must never contain exchange API keys, Discord webhook URLs,
member-dashboard secrets, account identifiers, or live positions.

## Ingestion contract

Run `limit-bracket-signal` after a completed source candle and hand the JSON to
the private operations repository. Validate the snapshot against
[`crypto-bracket-signal-v1.schema.json`](crypto-bracket-signal-v1.schema.json),
then apply these checks before any paper action or notification:

1. Reject an unknown `schema_version` or `event_type`.
2. Reject stale data using `source.closed_bar_timestamp`; do not substitute a
   forming candle or a guessed execution price.
3. Deduplicate the decision by the top-level `idempotency_key` and each
   candidate by its candidate `idempotency_key`.
4. Persist both actionable and `no_trade` snapshots in the append-only audit
   record. A no-trade decision is useful information, not a missing event.
5. Re-check current equity, open positions, symbol leverage cap, margin/funding
   cost, order-size limits, and venue availability in the private risk gate.
6. Paper-execute only an accepted candidate. The entry remains a limit order;
   there is no market-order fallback. Attach the protective stop and take-profit
   only after a fill is confirmed, and cancel a pending entry on expiry or a
   regime flip.
7. Fan out the same accepted snapshot and audit status to Discord and the
   member dashboard. Display the difference between `actionable`, `paper
   accepted`, `paper rejected`, and `no_trade`.

The v1 adapter translates a candidate only after the private risk gate accepts
it. It does not flatten a short or a bracket order into the old long-only
market `paper-step` format, because that would silently discard the entry,
stop, target, expiry, and reduce-only semantics. Private-gate rejections are
recorded locally and are not published as actionable Discord/dashboard
decisions.

## What the UI should show

At the top, show the decision (`BUY`, `SHORT`, or `WAIT`), the closed-candle
timestamp, venue/market, and regime confidence. Below that, show the ranked
symbols and, for each candidate, the planned limit entry, protective stop,
take-profit, expiry, maximum holding period, risk budget, and leverage cap.

When `decision` is `no_trade`, show `no_trade_reason` and the guardrails instead
of an empty alert. This prevents a neutral regime, warm-up period, or missing
retest from being mistaken for a system failure.

The event is a `research_candidate_only` signal. It is not permission to place
live orders; live execution remains disabled until a separate design, review,
and explicit promotion process exists. The current private adapter has no live
order client or withdrawal path.
