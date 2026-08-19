from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.models import OHLCVBar
from crypto_simulator.portfolio import PortfolioConfig
from crypto_simulator.spike_fade import (
    SpikeFadeSpec,
    evaluate_spike_fade_result,
    run_spike_fade_backtest,
)


def make_event_universe(count: int = 100) -> dict[str, list[OHLCVBar]]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    universe: dict[str, list[OHLCVBar]] = {}
    for symbol in ("BTC", "ALT"):
        bars: list[OHLCVBar] = []
        price = Decimal("100")
        for index in range(count):
            if symbol == "ALT" and index in (50, 51, 52):
                price = Decimal(("130", "165", "190")[index - 50])
            elif symbol == "ALT" and index in (53, 54, 55):
                price = Decimal(("175", "150", "120")[index - 53])
            elif symbol == "ALT" and index > 55:
                price = max(Decimal("100"), price - Decimal("1"))
            open_ = price if not bars else bars[-1].close
            high = max(open_, price) + Decimal("2")
            low = min(open_, price) - Decimal("2")
            volume = Decimal("1000") if symbol == "ALT" and 50 <= index <= 52 else Decimal("100")
            bars.append(
                OHLCVBar(
                    "test",
                    symbol,
                    "perpetual",
                    start + timedelta(hours=4 * index),
                    open_,
                    high,
                    low,
                    price,
                    volume,
                )
            )
        universe[symbol] = bars
    return universe


def event_spec(**overrides: object) -> SpikeFadeSpec:
    values: dict[str, object] = {
        "name": "test_spike_fade",
        "market": "perpetual",
        "pump_lookback": 3,
        "pump_return_threshold": 0.2,
        "volume_window": 10,
        "volume_multiple": 2,
        "atr_window": 5,
        "pump_atr_multiple": 2,
        "confirmation_window": 3,
        "rejection_fraction": 0.3,
        "stop_atr": 0.5,
        "take_profit_r": 1,
        "max_holding_bars": 8,
        "cooldown_bars": 1,
        "risk_per_trade": 0.05,
        "max_positions": 1,
        "max_gross_leverage": 1,
        "symbol_max_leverage": 1,
        "regime_fast": 5,
        "regime_slow": 10,
    }
    values.update(overrides)
    return SpikeFadeSpec(**values)


def test_spike_fade_waits_for_rejection_and_can_short_a_pump() -> None:
    result = run_spike_fade_backtest(
        make_event_universe(),
        event_spec(),
        PortfolioConfig(initial_cash=Decimal("100000"), fee_bps=0, slippage_bps=0, max_gross_leverage=1),
        benchmark_symbol="BTC",
    )

    assert result.signals
    assert all(signal["signal_index"] > 52 for signal in result.signals)
    assert [trade.side for trade in result.trades] == ["sell", "buy"]
    assert evaluate_spike_fade_result(result).total_return > 0


def test_spike_fade_respects_symbol_leverage_cap() -> None:
    result = run_spike_fade_backtest(
        make_event_universe(),
        event_spec(risk_per_trade=1, max_gross_leverage=5, symbol_max_leverage=5),
        PortfolioConfig(
            initial_cash=Decimal("100000"),
            fee_bps=0,
            slippage_bps=0,
            max_gross_leverage=5,
            max_leverage_by_symbol={"ALT": Decimal("3")},
        ),
        benchmark_symbol="BTC",
    )

    entry = next(trade for trade in result.trades if trade.side == "sell")
    assert entry.notional <= Decimal("300000")
