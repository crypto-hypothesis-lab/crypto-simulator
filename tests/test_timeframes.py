from datetime import datetime, timedelta, timezone

from crypto_simulator.models import OHLCVBar
from crypto_simulator.timeframes import resample_ohlcv


def hourly_bars(count: int = 24) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        OHLCVBar(
            "test",
            "BTC",
            "spot",
            start + timedelta(hours=index),
            str(100 + index),
            str(105 + index),
            str(95 + index),
            str(101 + index),
            "2",
            "200",
        )
        for index in range(count)
    ]


def test_resample_emits_only_complete_aligned_buckets() -> None:
    bars = resample_ohlcv(hourly_bars(), "4hour")

    assert len(bars) == 6
    assert bars[0].timestamp.isoformat() == "2025-01-01T00:00:00+00:00"
    assert bars[0].open == 100
    assert bars[0].high == 108
    assert bars[0].low == 95
    assert bars[0].close == 104
    assert bars[0].volume == 8
    assert bars[0].quote_volume == 800


def test_resample_drops_buckets_with_a_missing_source_candle() -> None:
    source = hourly_bars()
    bars = resample_ohlcv(source[:3] + source[4:], "4hour")

    assert len(bars) == 5
