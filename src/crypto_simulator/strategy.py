from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import OHLCVBar
from .timeframes import resample_ohlcv


@dataclass(frozen=True, slots=True)
class Signal:
    action: str
    reason: str


class SmaCrossStrategy:
    """Small deterministic baseline used to validate the execution model."""

    def __init__(self, fast_window: int = 20, slow_window: int = 50) -> None:
        if fast_window <= 0 or slow_window <= fast_window:
            raise ValueError("slow_window must be greater than fast_window > 0")
        self.fast_window = fast_window
        self.slow_window = slow_window

    def signal(self, bars: list[OHLCVBar]) -> Signal:
        if len(bars) < self.slow_window:
            return Signal("hold", "insufficient_history")
        fast = sum((bar.close for bar in bars[-self.fast_window:]), Decimal("0")) / self.fast_window
        slow = sum((bar.close for bar in bars[-self.slow_window:]), Decimal("0")) / self.slow_window
        if fast > slow:
            return Signal("buy", "fast_sma_above_slow_sma")
        return Signal("sell", "fast_sma_at_or_below_slow_sma")


@dataclass(frozen=True, slots=True)
class MultiTimeframeSignal:
    """The independent decisions made by the three timeframe layers."""

    action: str
    reason: str
    execution: Signal
    trend: Signal
    regime: Signal


class MultiTimeframeStrategy:
    """Long-only strategy with execution, trend, and regime layers.

    The input is expected to contain closed source candles. The strategy
    aggregates those candles into complete 4-hour and 1-day bars and therefore
    cannot accidentally use a still-forming higher-timeframe candle.
    """

    def __init__(
        self,
        execution_fast: int = 20,
        execution_slow: int = 50,
        trend_fast: int = 5,
        trend_slow: int = 20,
        regime_fast: int = 5,
        regime_slow: int = 20,
    ) -> None:
        self.execution = SmaCrossStrategy(execution_fast, execution_slow)
        self.trend = SmaCrossStrategy(trend_fast, trend_slow)
        self.regime = SmaCrossStrategy(regime_fast, regime_slow)
        self.execution_windows = (execution_fast, execution_slow)
        self.trend_windows = (trend_fast, trend_slow)
        self.regime_windows = (regime_fast, regime_slow)
        self._cache_first_timestamp = None
        self._cache_last_timestamp = None
        self._cache_source_count = 0
        self._cached_four_hour: list[OHLCVBar] = []
        self._cached_daily: list[OHLCVBar] = []

    def _higher_timeframe_bars(self, ordered: list[OHLCVBar]) -> tuple[list[OHLCVBar], list[OHLCVBar]]:
        if not ordered:
            return [], []
        cache_can_extend = (
            self._cache_source_count > 0
            and len(ordered) >= self._cache_source_count
            and ordered[0].timestamp == self._cache_first_timestamp
            and ordered[self._cache_source_count - 1].timestamp == self._cache_last_timestamp
        )
        if not cache_can_extend:
            self._cached_four_hour = resample_ohlcv(ordered, "4hour")
            self._cached_daily = resample_ohlcv(ordered, "1day")
        elif len(ordered) > self._cache_source_count:
            new_count = len(ordered) - self._cache_source_count
            # Only the suffix can contain newly completed higher-timeframe
            # buckets. Keeping one full bucket of overlap also lets a bucket
            # that was incomplete on the previous call become complete now.
            recent_four_hour = ordered[-(4 + new_count):]
            recent_daily = ordered[-(24 + new_count):]
            new_four_hour = resample_ohlcv(recent_four_hour, "4hour")
            new_daily = resample_ohlcv(recent_daily, "1day")
            self._cached_four_hour = self._merge_timeframe_cache(self._cached_four_hour, new_four_hour)
            self._cached_daily = self._merge_timeframe_cache(self._cached_daily, new_daily)
        self._cache_first_timestamp = ordered[0].timestamp
        self._cache_last_timestamp = ordered[-1].timestamp
        self._cache_source_count = len(ordered)
        return self._cached_four_hour, self._cached_daily

    @staticmethod
    def _merge_timeframe_cache(
        cached: list[OHLCVBar],
        additions: list[OHLCVBar],
    ) -> list[OHLCVBar]:
        by_timestamp = {bar.timestamp: bar for bar in cached}
        by_timestamp.update({bar.timestamp: bar for bar in additions})
        return [by_timestamp[key] for key in sorted(by_timestamp)]

    def _signal_sorted(self, ordered: list[OHLCVBar]) -> MultiTimeframeSignal:
        execution_signal = self.execution.signal(ordered)
        four_hour_bars, daily_bars = self._higher_timeframe_bars(ordered)
        trend_signal = self.trend.signal(four_hour_bars)
        regime_signal = self.regime.signal(daily_bars)

        if execution_signal.action == "sell":
            return MultiTimeframeSignal(
                "sell",
                "execution_layer_exit",
                execution_signal,
                trend_signal,
                regime_signal,
            )
        if trend_signal.action == "sell":
            return MultiTimeframeSignal(
                "sell",
                "four_hour_trend_filter_exit",
                execution_signal,
                trend_signal,
                regime_signal,
            )
        if regime_signal.action == "sell":
            return MultiTimeframeSignal(
                "sell",
                "daily_regime_filter_exit",
                execution_signal,
                trend_signal,
                regime_signal,
            )
        if "hold" in {execution_signal.action, trend_signal.action, regime_signal.action}:
            missing = ",".join(
                name
                for name, decision in (
                    ("execution", execution_signal),
                    ("4h_trend", trend_signal),
                    ("1d_regime", regime_signal),
                )
                if decision.action == "hold"
            )
            return MultiTimeframeSignal(
                "hold",
                f"insufficient_{missing}_history",
                execution_signal,
                trend_signal,
                regime_signal,
            )
        if execution_signal.action == "buy":
            return MultiTimeframeSignal(
                "buy",
                "execution_buy_all_higher_timeframe_filters_pass",
                execution_signal,
                trend_signal,
                regime_signal,
            )
        return MultiTimeframeSignal(
            "hold",
            "no_long_entry",
            execution_signal,
            trend_signal,
            regime_signal,
        )

    def signal_sorted(self, bars: list[OHLCVBar]) -> MultiTimeframeSignal:
        """Evaluate an already UTC-sorted history without copying or sorting it."""

        return self._signal_sorted(bars)

    def signal(self, bars: list[OHLCVBar]) -> MultiTimeframeSignal:
        return self._signal_sorted(sorted(bars, key=lambda bar: bar.timestamp))
