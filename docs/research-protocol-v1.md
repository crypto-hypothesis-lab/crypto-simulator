# Research Protocol v1

This protocol is the fixed comparison contract for Paper-strategy research.
It is intentionally stricter than a one-off backtest and is not an automatic
promotion mechanism.

## Research lines

The current candidate set is limited to three causal hypotheses:

1. `Event Long Pullback`: participate only after a qualified pullback in a
   `risk_on` environment.
2. `Spike Fade Short`: short an exhaustion event only after closed-candle
   confirmation, with Funding and liquidity evidence where available.
3. `Trend Entry + Regime Router`: use trend/ranking for entry, while regime is
   only a permission filter. `neutral`, stale, or stress conditions mean no
   new entry.

The frozen v1 Paper profile remains the comparison baseline. The router v2
profile is research-only until it passes the same protocol.

## Fixed rules

- Signals use closed candles only. No future candle, current incomplete higher
  timeframe candle, or future universe membership may be used.
- The benchmark, symbol universe, interval, fee, slippage, spread, market
  impact, funding, and financing assumptions are recorded in every report.
- MEXC perpetual research uses the configured liquid universe snapshot and
  one-hour candles. The stable four-symbol set is retained only as a
  comparison baseline.
- Paper risk is 0.10% per candidate trade for the event candidates and the
  default leverage ceiling is a cap, not a target. Research comparisons use
  1x unless the report explicitly says otherwise.
- A limit entry never falls back to a market order. Stop-first handling is
  used when a stop and target are both touched in one bar.
- Maximum holding time is at most 30 days; event candidates use their explicit
  short holding windows.

## Evaluation order

`Backtest -> Walk Forward -> Cost Stress -> Forward Test -> Paper -> Shadow Live`

The following checks are required before a candidate can be considered for
the next stage:

- at least three chronological out-of-sample windows for the current data
  generation, with more windows required as history grows;
- positive out-of-sample expectancy and no single symbol responsible for the
  result;
- Profit Factor above 1.20 where the sample is large enough to interpret it;
- maximum drawdown within the account risk budget;
- cost stress does not turn the thesis into a strongly negative result;
- enough distinct trading days and filled/unfilled attempts recorded to avoid
  treating a handful of fills as evidence;
- Backtest, Forward Test, and Paper carry the same `strategy_id`, immutable
  `strategy_version`, and signal identity.

These are promotion requirements, not claims that a candidate is profitable.
Small samples remain `hold` even when the point estimate is positive.
An OOS window with no qualified training candidate is `NO_STRATEGY / NO_TRADE`.
It is neither a win nor a loss. A strategy with zero events is `unmeasured`,
and may not win a selection step merely because its drawdown is zero.

Every report, including a rejected hypothesis, is recorded in the research
ledger before a replacement strategy is evaluated. The next experiment must
be able to query the prior strategy version, dataset period, costs, result,
and Walk Forward outcome classification.

## Exit experiments

Exit changes are tested one at a time, in this order:

`baseline -> break-even -> partial take-profit -> trailing -> regime exit`

Each experiment gets a new strategy version and a separate report. Combining
all exits in one experiment is prohibited because it prevents attribution.

## Diagnostics

Every closed bracket records MAE/MFE in initial-risk units (`R`). MAE/MFE is
diagnostic only and does not change exits. A future report may classify losses
as entry failure, timing, stop distance, volatility, liquidity, or
Funding/OI stress only when the required features are available.

The protocol deliberately does not promise a profitable strategy. Its purpose
is to make weak hypotheses cheap to reject and to stop a flattering
full-sample result from becoming Paper authority.
