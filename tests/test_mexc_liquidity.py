from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.adapters.mexc_contract import MexcTicker
from crypto_simulator.mexc_liquidity import LiquidityPolicy, assess_liquidity, select_current_liquid_tickers
from crypto_simulator.models import OHLCVBar


def bars_for_days(daily_amounts: list[str]) -> list[OHLCVBar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for day, amount in enumerate(daily_amounts):
        for hour in range(24):
            timestamp = start + timedelta(days=day, hours=hour)
            bars.append(
                OHLCVBar(
                    "mexc",
                    "ALT_USDT",
                    "perpetual",
                    timestamp,
                    "100",
                    "101",
                    "99",
                    "100",
                    "1",
                    Decimal(amount) / 24,
                )
            )
    return bars


def ticker(symbol: str, amount: str, spread: str = "0.02") -> MexcTicker:
    half_spread = Decimal(spread) / 2
    return MexcTicker(
        symbol,
        Decimal("100"),
        Decimal("100") - half_spread,
        Decimal("100") + half_spread,
        Decimal(amount),
        Decimal("1"),
        Decimal("1"),
        datetime(2026, 1, 30, tzinfo=timezone.utc),
    )


def test_liquidity_classifies_gradual_rise_and_allows_it() -> None:
    policy = LiquidityPolicy(
        min_quote_turnover_24h=Decimal("100"),
        min_median_daily_quote_turnover=Decimal("100"),
        min_history_bars=24,
        max_spread_bps=5,
    )
    assessment = assess_liquidity(
        "ALT_USDT",
        bars_for_days(["100"] * 14 + ["130"] * 7),
        policy=policy,
        spread_bps=Decimal("2"),
    )

    assert assessment.passed is True
    assert assessment.volume_regime == "rising"
    assert assessment.size_multiplier == policy.rising_size_multiplier


def test_liquidity_keeps_a_well_based_surge_but_reduces_size() -> None:
    policy = LiquidityPolicy(
        min_quote_turnover_24h=Decimal("100"),
        min_median_daily_quote_turnover=Decimal("100"),
        min_history_bars=24,
        max_spread_bps=5,
    )
    assessment = assess_liquidity(
        "ALT_USDT",
        bars_for_days(["100"] * 20 + ["400"]),
        policy=policy,
        spread_bps=Decimal("2"),
    )

    assert assessment.passed is True
    assert assessment.volume_regime == "surging"
    assert assessment.size_multiplier == policy.surge_size_multiplier


def test_current_liquidity_selection_keeps_benchmark_and_respects_spread() -> None:
    policy = LiquidityPolicy(
        max_symbols=2,
        min_quote_turnover_24h=Decimal("100"),
        max_spread_bps=5,
    )
    selected = select_current_liquid_tickers(
        [ticker("BTC_USDT", "1000"), ticker("ETH_USDT", "900"), ticker("WIDE_USDT", "2000", spread="0.2")],
        policy=policy,
        benchmark_symbol="BTC_USDT",
    )

    assert [item.symbol for item in selected] == ["BTC_USDT", "ETH_USDT"]
