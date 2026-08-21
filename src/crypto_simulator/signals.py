from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .models import OHLCVBar
from .strategy import MultiTimeframeSignal, MultiTimeframeStrategy, SmaCrossStrategy
from .timeframes import interval_duration, resample_ohlcv


SIGNAL_SCHEMA_VERSION = "crypto.signal.v1"
SIGNAL_SOURCE = "crypto-simulator"


def _signal_identity(*, latest: OHLCVBar, interval: str, strategy_id: str, action: str, candle_close_at: datetime) -> tuple[str, str]:
    """Build a stable identity for one closed-candle decision."""

    signal_key = ":".join(
        (
            latest.exchange,
            latest.symbol,
            interval,
            candle_close_at.astimezone(timezone.utc).isoformat(),
            strategy_id,
            action,
        )
    )
    return signal_key, signal_key


def closed_bars(
    bars: list[OHLCVBar],
    interval: str,
    *,
    as_of: datetime | None = None,
) -> list[OHLCVBar]:
    try:
        duration = interval_duration(interval)
    except ValueError as exc:
        raise ValueError(f"unsupported signal interval: {interval}") from exc
    as_of = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return [bar for bar in sorted(bars, key=lambda item: item.timestamp) if bar.timestamp + duration <= as_of]


def build_signal_event(
    bars: list[OHLCVBar],
    *,
    interval: str,
    fast_window: int = 20,
    slow_window: int = 50,
    trend_fast_window: int = 5,
    trend_slow_window: int = 20,
    regime_fast_window: int = 5,
    regime_slow_window: int = 20,
    multi_timeframe: bool = False,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    usable_bars = closed_bars(bars, interval, as_of=as_of)
    if not usable_bars:
        raise ValueError("no closed bars available")
    strategy = (
        MultiTimeframeStrategy(
            execution_fast=fast_window,
            execution_slow=slow_window,
            trend_fast=trend_fast_window,
            trend_slow=trend_slow_window,
            regime_fast=regime_fast_window,
            regime_slow=regime_slow_window,
        )
        if multi_timeframe
        else SmaCrossStrategy(fast_window, slow_window)
    )
    latest = usable_bars[-1]
    signal = strategy.signal(usable_bars)
    strategy_id = f"sma_cross_{fast_window}_{slow_window}"
    if multi_timeframe:
        strategy_id = (
            f"mtf_sma_cross_{fast_window}_{slow_window}"
            f"__4h_sma_{trend_fast_window}_{trend_slow_window}"
            f"__1d_sma_{regime_fast_window}_{regime_slow_window}"
        )
    execution_duration = interval_duration(interval)
    candle_close_at = latest.timestamp + execution_duration
    signal_key, event_id = _signal_identity(
        latest=latest,
        interval=interval,
        strategy_id=strategy_id,
        action=signal.action,
        candle_close_at=candle_close_at,
    )
    event: dict[str, Any] = {
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "signal_source": SIGNAL_SOURCE,
        "event_id": event_id,
        "signal_key": signal_key,
        "event_type": "PAPER_SIGNAL",
        "strategy_id": strategy_id,
        "strategy_version": strategy_id,
        "exchange": latest.exchange,
        "symbol": latest.symbol,
        "market_type": latest.market_type,
        "interval": interval,
        "timestamp": latest.timestamp.isoformat(),
        "candle_close_at": candle_close_at.isoformat(),
        "price": str(latest.close),
        "action": signal.action,
        "reason": signal.reason,
        "history_bars": len(usable_bars),
    }
    if not multi_timeframe:
        return event

    assert isinstance(signal, MultiTimeframeSignal)
    execution_timestamp = latest.timestamp + execution_duration
    next_bars = [bar for bar in sorted(bars, key=lambda item: item.timestamp) if bar.timestamp == execution_timestamp]
    if not next_bars:
        raise ValueError("next execution candle is required for a multi-timeframe signal")
    execution_bar = next_bars[0]
    event.update(
        {
            "strategy_id": strategy_id,
            "decision_timestamp": latest.timestamp.isoformat(),
            "decision_price": str(latest.close),
            "execution_timestamp": execution_timestamp.isoformat(),
            "execution_price": str(execution_bar.open),
            "execution_price_source": "next_candle_open",
            "timestamp": execution_timestamp.isoformat(),
            "price": str(execution_bar.open),
            "action": signal.action,
            "reason": signal.reason,
            "timeframes": {
                "execution": {
                    "interval": interval,
                    "action": signal.execution.action,
                    "reason": signal.execution.reason,
                    "bars": len(usable_bars),
                },
                "trend": {
                    "interval": "4hour",
                    "action": signal.trend.action,
                    "reason": signal.trend.reason,
                    "bars": len(resample_ohlcv(usable_bars, "4hour")),
                },
                "regime": {
                    "interval": "1day",
                    "action": signal.regime.action,
                    "reason": signal.regime.reason,
                    "bars": len(resample_ohlcv(usable_bars, "1day")),
                },
            },
        }
    )
    return event
