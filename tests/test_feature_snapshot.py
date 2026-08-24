from datetime import datetime, timezone
from decimal import Decimal

from crypto_simulator.feature_snapshot import build_feature_snapshot
from crypto_simulator.models import OHLCVBar


def bars(count: int = 30) -> list[OHLCVBar]:
    start = datetime(2026, 8, 23, tzinfo=timezone.utc)
    return [OHLCVBar(
        exchange="mexc",
        symbol="BTC_USDT",
        market_type="perpetual",
        timestamp=start.replace(hour=(start.hour + index) % 24, day=start.day + (start.hour + index) // 24),
        open=Decimal(100 + index),
        high=Decimal(101 + index),
        low=Decimal(99 + index),
        close=Decimal(100 + index),
        volume=Decimal(10 + index),
    ) for index in range(count)]


def test_feature_snapshot_is_closed_deterministic_and_shared() -> None:
    snapshot = build_feature_snapshot(bars(), interval="1hour", as_of=datetime(2026, 8, 24, 8, tzinfo=timezone.utc))
    assert snapshot.schema_version == "crypto.feature-snapshot.v1"
    assert snapshot.data_quality == "fresh"
    assert snapshot.features["atr"] is not None
    assert snapshot.features["volume_zscore"] is not None
    assert snapshot.to_dict()["bars_hash"] == snapshot.bars_hash


def test_feature_snapshot_marks_candle_gaps_degraded() -> None:
    values = bars()
    values.pop(10)
    snapshot = build_feature_snapshot(values, interval="1hour", as_of=datetime(2026, 8, 24, 8, tzinfo=timezone.utc))
    assert snapshot.missing_bars == 1
    assert snapshot.data_quality == "degraded"
