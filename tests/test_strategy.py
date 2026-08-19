from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.models import OHLCVBar
from crypto_simulator.strategy import MultiTimeframeStrategy


def _hourly_bars(count: int = 120) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        OHLCVBar(
            "test",
            "BTC/JPY",
            "spot",
            start + timedelta(hours=index),
            Decimal(100 + index),
            Decimal(101 + index),
            Decimal(99 + index),
            Decimal(100 + index),
            Decimal("1"),
        )
        for index in range(count)
    ]


def test_incremental_higher_timeframe_cache_matches_fresh_evaluation() -> None:
    source = _hourly_bars()
    cached = MultiTimeframeStrategy(2, 3, 2, 3, 2, 3)

    for index in range(1, len(source) + 1):
        cached_signal = cached.signal_sorted(source[:index])
        fresh_signal = MultiTimeframeStrategy(2, 3, 2, 3, 2, 3).signal(source[:index])
        assert cached_signal == fresh_signal
