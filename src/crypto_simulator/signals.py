from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import OHLCVBar
from .strategy import SmaCrossStrategy


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


def closed_bars(
    bars: list[OHLCVBar],
    interval: str,
    *,
    as_of: datetime | None = None,
) -> list[OHLCVBar]:
    try:
        duration = _INTERVALS[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported signal interval: {interval}") from exc
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return [bar for bar in sorted(bars, key=lambda item: item.timestamp) if bar.timestamp + duration <= as_of]


def build_signal_event(
    bars: list[OHLCVBar],
    *,
    interval: str,
    fast_window: int = 20,
    slow_window: int = 50,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    usable_bars = closed_bars(bars, interval, as_of=as_of)
    if not usable_bars:
        raise ValueError("no closed bars available")
    strategy = SmaCrossStrategy(fast_window, slow_window)
    latest = usable_bars[-1]
    signal = strategy.signal(usable_bars)
    strategy_id = f"sma_cross_{fast_window}_{slow_window}"
    return {
        "event_id": f"{latest.exchange}:{latest.symbol}:{latest.epoch_ms}:{strategy_id}",
        "event_type": "PAPER_SIGNAL",
        "strategy_id": strategy_id,
        "exchange": latest.exchange,
        "symbol": latest.symbol,
        "market_type": latest.market_type,
        "interval": interval,
        "timestamp": latest.timestamp.isoformat(),
        "price": str(latest.close),
        "action": signal.action,
        "reason": signal.reason,
        "history_bars": len(usable_bars),
    }
