from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.backtest import BacktestConfig, run_backtest
from crypto_simulator.models import OHLCVBar
from crypto_simulator.research import StrategySpec, dataset_quality, evaluate_result, research_report, walk_forward_search
from crypto_simulator.strategy import SmaCrossStrategy


def bars(days: int = 40) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    result = []
    for index in range(days * 24):
        # A deterministic trend with a repeating pullback creates both entries
        # and exits without relying on a network dataset.
        close = Decimal(100 + index // 12 + (index % 24 < 12) * 2)
        result.append(
            OHLCVBar(
                "test",
                "BTC/JPY",
                "spot",
                start + timedelta(hours=index),
                close,
                close,
                close,
                close,
                "1",
            )
        )
    return result


def test_evaluate_result_includes_buy_and_hold_and_costs() -> None:
    dataset = bars(5)
    config = BacktestConfig(initial_cash=Decimal("1000"), fee_bps=Decimal("10"), slippage_bps=Decimal("5"))
    result = run_backtest(dataset, SmaCrossStrategy(2, 3), config)
    metrics = evaluate_result(result, dataset, config)

    assert metrics.benchmark_return != 0
    assert metrics.trades >= 0
    assert metrics.exposure >= 0


def test_walk_forward_uses_history_for_warmup_and_returns_windows() -> None:
    dataset = bars(40)
    specs = [StrategySpec("test_single", 2, 3, single_timeframe=True)]
    windows = walk_forward_search(
        dataset,
        specs,
        BacktestConfig(fee_bps=Decimal("0"), slippage_bps=Decimal("0")),
        train_days=20,
        test_days=5,
        step_days=5,
    )

    assert windows
    assert windows[0].selected_strategy == "test_single"
    assert windows[0].test_metrics.exposure >= 0


def test_research_report_marks_short_dataset_as_unvalidated() -> None:
    report = research_report(bars(3), [StrategySpec("test_single", 2, 3, single_timeframe=True)])

    assert report["summary"]["walk_forward_windows"] == 0
    assert report["summary"]["status"] == "insufficient_history_for_walk_forward"


def test_dataset_quality_reports_missing_and_duplicate_candles() -> None:
    dataset = bars(2)
    gappy = dataset[:10] + dataset[11:] + [dataset[0]]

    quality = dataset_quality(gappy)

    assert quality["missing_bars"] == 1
    assert quality["duplicate_bars"] == 1
    assert quality["gap_count"] == 1
    assert quality["contiguous"] is False
