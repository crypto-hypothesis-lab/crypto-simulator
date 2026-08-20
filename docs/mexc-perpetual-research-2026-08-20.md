# MEXC perpetual research snapshot (2026-08-20)

This is a research/Paper result only. It is not an order instruction and was not
connected to the notifier or a live account.

## Data

- Source: MEXC public contract kline and public funding-history endpoints.
- Universe: `BTC_USDT`, `ETH_USDT`, `SOL_USDT`, `XRP_USDT`.
- Daily portfolio set: 2024-08-20 through 2026-08-20, 731 complete bars per symbol.
- Intraday bracket set: 2025-08-20 through 2026-08-20, 2,191 complete 4-hour bars per symbol.
- Price data quality: 100% coverage, no duplicate bars, no gaps in the synchronized sets.
- Funding history was included where available; the downloaded funding series begins
  on 2025-02-26, so funding coverage is shorter than the price history.

MEXC documents the contract kline endpoint as a public endpoint with a maximum of
2,000 bars per request and the funding history endpoint as paginated public data:
<https://mexcdevelop.github.io/apidocs/contract_v1_en/>.

## Candidate comparison

Costs were intentionally conservative: 5 bp fee, 5 bp slippage, 10 bp spread,
and 5 bp market impact. All results use next-bar execution and include funding
settlements for perpetual positions.

### Regime momentum, daily bars

The best full-sample candidate was `perp_regime_momo_21_63_top2`, which selects
relative-momentum leaders/laggards under a BTC regime and allows both long and
short weights.

| Gross cap | Full-sample return | Max drawdown | Positive OOS excess windows | Worst OOS return | Status |
| --- | ---: | ---: | ---: | ---: | --- |
| 1x | 11.45% | 29.66% | 5/9 | -30.76% | not validated |
| 2x | 17.99% | 40.98% | 5/9 | -44.26% | not validated |
| 3x | 17.67% | 51.09% | 5/9 | -56.05% | not validated |
| 5x | 21.75% | 57.34% | 6/9 | -60.98% | requires forward test |

The 5x run is not acceptable as a production candidate: the positive full-sample
result is accompanied by a very large drawdown and a severe negative OOS window.

### ATR limit-entry bracket, 4-hour bars

The balanced candidate is:

`perpetual_limit_retest_balanced_0p35_1p5_2R_14d`

- Long and short entries are allowed only in their matching BTC regime.
- Entry uses a resting pullback limit, not a market fallback.
- Stop: 1.5 ATR.
- Target: 2.0R.
- Limit expiry: 8 bars.
- Maximum holding period: 14 days.
- Risk budget: 0.4% per trade.
- Gross exposure ceiling: 5x; observed maximum exposure was only 0.36x because
  the risk budget and ATR stop constrained position size.
- Full sample: -3.07% return, 6.19% maximum drawdown, 55 round trips,
  18 long and 37 short.
- The three OOS windows were -1.27%, -1.15%, and -1.26%: lower risk, but not
  a profitable validated strategy.

The spike-fade short-only candidates produced no meaningful number of trades in
the same 1-year sample and were rejected as insufficient evidence.

## Decision

No MEXC candidate is currently proven profitable after realistic costs and
walk-forward testing. The safest candidate to carry into Paper forward testing
is the balanced ATR limit-entry bracket with the 5x value retained only as a
hard cap. It must remain disabled for live execution until a longer forward
sample demonstrates positive net return, controlled drawdown, and enough trades.

## Event-filtered 1-hour follow-up

The new event profiles were also evaluated on the same four-contract universe
using 8,761 synchronized 1-hour bars per symbol from 2025-08-20 through
2026-08-20. Price coverage was complete and the public Funding series was
included where available.

| Candidate | Round trips | Full return | Max drawdown | Win rate | Positive OOS windows | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `mexc_event_long_pullback_atr_v1` | 26 | +1.20% | 0.35% | 61.5% | 1/3 | `not_validated` |
| `mexc_event_short_daily_red_green_3_4_v1` | 0 | 0.00% | 0.00% | 0.0% | 0/3 meaningful | `not_validated` |

The long candidate is more selective and had a smaller observed drawdown than
the standard balanced bracket on this sample (105 round trips, +0.87% return,
5.05% drawdown, 38.1% win rate), but 26 trades are not enough evidence. Its
walk-forward returns were +0.30%, -0.13%, and 0.00% across the three test
windows. The short event condition did not trigger in the test period, so it
has no evidence of profitability yet.

The promotion gate used 28 observed attempts for the long candidate (26 fills
and 2 cancellations). Fill rate was 92.9%, but the average net return was only
about +0.28%; after the additional 0.51% cost reserve, effective expectancy was
-0.21% and the 95% lower bound was -0.73%. The result is therefore `hold`, not
Paper promotion.

The latest 2026-08-20 snapshot was `no_trade` for both event profiles. The
long profile saw `risk_on`, but no valid pullback candidate; the short profile
correctly rejected the same `risk_on` regime. The generated artifacts are
`state/mexc-event-research-2026-08-20.json`,
`state/mexc-event-promotion-2026-08-20.json`, and the two latest signal files.

## Expanded liquid crypto universe follow-up

The current MEXC ticker snapshot contained 1,128 perpetual tickers. After
24-hour quote-turnover and 25 bp spread filters, contract classification, and a
BTC benchmark requirement, the top-12 research set was:

`BTC_USDT`, `ETH_USDT`, `SOL_USDT`, `XRP_USDT`, `HYPE_USDT`, `ZEC_USDT`,
`PEPE_USDT`, `DOGE_USDT`, `BTW_USDT`, `LINK_USDT`, `SUI_USDT`, and `TAO_USDT`.

`BTW_USDT` had no historical candles and was rejected. The remaining 11
contracts each had 8,761 complete one-hour bars, no gaps, and passed the
historical liquidity audit. The current universe selection intentionally
excluded high-volume stock/ETF/commodity contracts such as `SOXL_USDT` and
`XAU_USDT`; high turnover alone is not enough to classify an altcoin.

On the 11-symbol set, the event long candidate produced 35 round trips,
`+1.55%` full-sample return, `0.37%` maximum drawdown, and `51.4%` win rate.
The event short candidate produced only one losing round trip, so it remains
unproven. Walk-forward returns for the selected event strategy were `0.00%`,
`+1.13%`, and `-0.27%`; the report status remained `not_validated`.

The expanded long result still failed promotion: 39 observed attempts, 26
distinct days, and a negative 95% lower confidence bound. The result is
recorded as `hold`. The expanded artifacts are
`state/mexc-liquid-universe-audited.json`,
`state/mexc-event-research-liquid-2026-08-20.json`, and
`state/mexc-event-promotion-liquid-2026-08-20.json`.
