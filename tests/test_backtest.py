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
