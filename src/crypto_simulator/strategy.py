from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import OHLCVBar


@dataclass(frozen=True)
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
