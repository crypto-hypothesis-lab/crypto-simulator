# MEXC event strategies

This document records the MEXC strategy candidates implemented in the
simulator. They are research and Paper candidates only; they are not live
order instructions.

## Candidates

### `mexc_event_short_daily_red_green_3_4_v1`

The short candidate requires all of the following at a closed 1-hour candle:

- the daily candle is `RED`;
- three to four consecutive 1-hour candles are green;
- the symbol has at least +5% one-hour relative return versus the BTC benchmark;
- the latest available Funding rate is at least `-0.05%` (`-0.0005` as a rate);
- the market regime is `risk_off`.

It places a resting short limit `0.75 ATR` above the signal close. The bracket
uses a `1.5 ATR` stop, `2R` target, four-bar order expiry, eight-hour maximum
holding time, 0.10% risk budget, one position, and a 3x hard gross cap.

### `mexc_event_long_pullback_atr_v1`

This candidate runs only in `risk_on`. It uses the existing cross-sectional
trend and breadth model, then places a long pullback limit `0.35 ATR` below the
signal close. It uses the same `1.5 ATR` stop, `2R` target, four-bar expiry,
eight-hour maximum holding time, 0.10% risk budget, one position, and 3x hard
gross cap.

### `mexc_event_long_permission_filter_v1`

This is the long-only permission experiment. A `risk_on` regime allows the
existing long event entry; `neutral` and `risk_off` block new entries. The
regime never selects a short strategy, and the research leverage cap is 1x.
Run it with:

```console
python -m crypto_simulator limit-bracket-research \
  --market perpetual \
  --profile mexc-event-permission \
  --benchmark-symbol BTC_USDT \
  --max-gross-leverage 1
```

The 2026-08-22 11-symbol run was positive full-sample but had zero positive
OOS windows and remains `not_validated`. This profile is therefore a safer
directional architecture, not a validated profitable strategy.

### `mexc_event_short_rejection_volume_v1`

This is a stricter short candidate for perpetuals. It requires a `risk_off`
regime, a `RED` daily candle, three to six green 1-hour candles immediately
before the current candle, at least `1.5x` the prior 20-bar median volume, a
red rejection candle closing in the lower 45% of its range, and at least +3%
four-hour relative return versus BTC. It uses a `0.50 ATR` resting entry
offset, a `1.35 ATR` stop, a `1.6R` target, four-bar expiry, a 12-hour maximum
holding time, 0.10% risk budget, one position, and a 5x hard ceiling.

On the local 11-symbol, approximately one-year MEXC perpetual sample with
funding and execution costs, it produced 5 round trips, 0 wins, -0.62% return,
and 0.64% maximum drawdown. It is therefore recorded for further research but
is `hold` and must not be promoted to Paper entry authority.

The fixed 1%/2% pullback variants from the external scanner remain comparison
hypotheses. They are not silently substituted for the ATR-normalized version.

## Liquidity universe

The initial four-symbol set (`BTC_USDT`, `ETH_USDT`, `SOL_USDT`, and
`XRP_USDT`) is only a stable comparison baseline. The simulator now selects up
to 20 current MEXC USDT perpetuals using 24-hour quote turnover and top-of-book
spread, then excludes MEXC contracts classified as stocks, ETFs, or
commodities. Each selected history is audited for coverage and quote turnover.

Liquidity is treated as a regime, not a single volume threshold:

- `stable`: the recent turnover is near its seven-day baseline;
- `rising`: the seven-day median is materially above the prior seven days;
- `surging`: the latest 24-hour turnover is materially above its own seven-day
  baseline.

`rising` candidates receive a 0.75 size multiplier and `surging` candidates a
0.50 multiplier. A surge is still rejected when the seven-day base turnover is
below the liquidity floor. This keeps genuine capital inflows visible while
preventing a one-day volume anomaly from receiving full position size.
The liquid-perpetual workflow converts this multiplier into a per-symbol
leverage ceiling: stable symbols may use the configured 5x research ceiling,
rising symbols 3.75x, and surging symbols 2.5x. This is a risk cap, not a
requirement to use leverage.

## Safety and promotion

Every candidate is tagged with `strategy_family` and its event features are
written into the signal evidence. The private Paper boundary can allow only an
explicit family, reject stale or duplicated signals, and enforce one new entry
per UTC day plus a same-symbol cooldown.

The `promotion-gate` command requires cost-adjusted effective expectancy across
the 20/50/100 outcome windows, at least 30 distinct days, and a non-negative
95% lower confidence bound. Unfilled limit attempts remain in the denominator.
Failure produces `hold`; it never promotes a strategy automatically.

## Current status

These candidates must be run against MEXC 1-hour candles with Funding data and
walk-forward validation. A positive full-sample result alone is insufficient.
Live execution remains outside this repository and is not enabled by these
changes.

## Router v2 research profile

`default_mexc_event_v2_specs()` exposes separate `*_router_v2` strategy IDs
for the same three event hypotheses. It adds a causal benchmark volatility
permission layer:

- `normal`: retain the v1 `risk_on`/`risk_off`/`neutral` decision;
- `stress`: benchmark realized volatility is at or above its trailing 90th
  percentile, so no new entry is allowed;
- `insufficient_volatility_history`: no entry is allowed until the rolling
  volatility history is long enough.

The public signal keeps the compatible regime label `neutral` during stress
and records the more specific state plus volatility metrics separately. This
avoids breaking the Operations contract while making the reason visible to
research and monitoring. The scheduled v1 Paper workflow does not use v2;
run `limit-bracket-research --profile mexc-event-v2` or the `*-v2` signal
profiles for isolated comparison only.
