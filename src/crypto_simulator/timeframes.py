from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .models import OHLCVBar


_INTERVALS = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "30min": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "1hour": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "4hour": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1day": timedelta(days=1),
}


def interval_duration(interval: str) -> timedelta:
    """Return the UTC duration represented by a supported candle interval."""

    try:
        return _INTERVALS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported interval: {interval}") from exc


def _bucket_start(timestamp: datetime, duration: timedelta) -> datetime:
    timestamp = timestamp.astimezone(timezone.utc)
    seconds = int(timestamp.timestamp())
    bucket_seconds = int(duration.total_seconds())
    return datetime.fromtimestamp(seconds - seconds % bucket_seconds, tz=timezone.utc)


def resample_ohlcv(
    bars: list[OHLCVBar],
    interval: str,
    *,
    source_interval: str = "1hour",
) -> list[OHLCVBar]:
    """Aggregate complete, UTC-aligned candles from a lower timeframe.

    Incomplete buckets and buckets with missing source candles are omitted. This
    is deliberate: a higher-timeframe filter must never use a partially formed
    candle or silently bridge a data gap.
    """

    target_duration = interval_duration(interval)
    source_duration = interval_duration(source_interval)
    target_seconds = int(target_duration.total_seconds())
    source_seconds = int(source_duration.total_seconds())
    if target_seconds <= source_seconds or target_seconds % source_seconds:
        raise ValueError("target interval must be a whole multiple of source interval")
    expected_count = target_seconds // source_seconds

    grouped: dict[tuple[str, str, str, datetime], dict[datetime, OHLCVBar]] = defaultdict(dict)
    for bar in bars:
        bucket = _bucket_start(bar.timestamp, target_duration)
        grouped[(bar.exchange, bar.symbol, bar.market_type, bucket)][bar.timestamp] = bar

    result: list[OHLCVBar] = []
    for (exchange, symbol, market_type, bucket), members in grouped.items():
        timestamps = sorted(members)
        expected_timestamps = [
            bucket + source_duration * index for index in range(expected_count)
        ]
        if timestamps != expected_timestamps:
            continue
        ordered = [members[timestamp] for timestamp in timestamps]
        quote_volume = None
        if all(bar.quote_volume is not None for bar in ordered):
            quote_volume = sum((bar.quote_volume for bar in ordered), Decimal("0"))
        result.append(
            OHLCVBar(
                exchange=exchange,
                symbol=symbol,
                market_type=market_type,
                timestamp=bucket,
                open=ordered[0].open,
                high=max(bar.high for bar in ordered),
                low=min(bar.low for bar in ordered),
                close=ordered[-1].close,
                volume=sum((bar.volume for bar in ordered), Decimal("0")),
                quote_volume=quote_volume,
            )
        )
    return sorted(result, key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
