from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.backtest import BacktestConfig, run_backtest
from crypto_simulator.models import OHLCVBar
from crypto_simulator.strategy import SmaCrossStrategy


def bars(values: list[str]) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [OHLCVBar("test", "BTC", "spot", start + timedelta(hours=i), value, value, value, value, "1") for i, value in enumerate(values)]


def test_backtest_uses_next_bar_open_and_accounts_for_fee() -> None:
    result = run_backtest(
        bars(["100", "99", "98", "101", "103", "105", "104", "102"]),
        SmaCrossStrategy(2, 3),
        BacktestConfig(initial_cash=Decimal("1000"), fee_bps=Decimal("10"), slippage_bps=Decimal("0")),
    )
    assert result.trades
    assert result.trades[0].side == "buy"
    assert result.trades[0].timestamp.endswith("04:00:00+00:00")
    assert result.final_equity > Decimal("0")


def test_backtest_forces_exit_after_maximum_holding_period() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = [
        OHLCVBar("test", "BTC", "spot", start, "90", "90", "90", "90", "1"),
        OHLCVBar("test", "BTC", "spot", start + timedelta(hours=1), "100", "100", "100", "100", "1"),
        OHLCVBar("test", "BTC", "spot", start + timedelta(hours=2), "110", "110", "110", "110", "1"),
        OHLCVBar("test", "BTC", "spot", start + timedelta(hours=2, days=30), "105", "105", "105", "105", "1"),
    ]
    result = run_backtest(
        bars,
        SmaCrossStrategy(1, 2),
        BacktestConfig(initial_cash=Decimal("1000"), fee_bps=Decimal("0"), slippage_bps=Decimal("0"), max_holding_days=30),
    )
    assert [trade.side for trade in result.trades] == ["buy", "sell"]
    assert result.trades[-1].reason == "max_holding_period"


def test_execution_cost_model_applies_half_spread_and_market_impact() -> None:
    config = BacktestConfig(
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("5"),
        spread_bps=Decimal("10"),
        market_impact_bps=Decimal("3"),
    )

    assert config.one_way_execution_bps == Decimal("13")
    assert config.execution_price(Decimal("100"), "buy") == Decimal("100.13")
    assert config.execution_price(Decimal("100"), "sell") == Decimal("99.87")
