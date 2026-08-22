# Cross-venue event permission research — 2026-08-23

The same long-only hypothesis was tested on Hyperliquid perpetuals and bitbank spot:

> Allow the event/pullback entry only when the causal regime is `risk_on`; `neutral` and `risk_off` block new entries and do not switch to shorts.

Both runs used 1-hour candles, one-times gross leverage, the same entry/exit parameters, 120-day training windows, 30-day OOS windows, a 20-trade minimum training floor, and venue-specific conservative fee assumptions.

## Result

| Venue | Full-sample return | PF | Round trips | Max DD | OOS status | Decision |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Hyperliquid perpetual | +1.20% | 2.28 | 26 | 0.37% | 0 qualified OOS windows; 2 no-trade windows | Not validated |
| bitbank spot | +0.03% | 1.05 | 7 | 0.27% | 0 qualified OOS windows; 2 no-trade windows | Not validated |

The Hyperliquid full-sample result is only a research signal: it uses approximately 209 available days rather than a full year, and no walk-forward window passed the minimum evidence gate. The bitbank result is not attractive even in-sample and underperformed the BTC buy-and-hold benchmark by roughly 16.58 percentage points over the sampled period.

Neither venue is promoted to Paper or Live. The raw ignored JSON reports remain local for inspection; this compact evidence file is the reproducible, reviewable record intended for GitHub.
