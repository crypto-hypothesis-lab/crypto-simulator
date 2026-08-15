from __future__ import annotations

from .models import OHLCVBar


def deduplicate_bars(bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Sort by timestamp and keep the last observation for duplicate timestamps."""

    by_epoch = {bar.epoch_ms: bar for bar in bars}
    return [by_epoch[key] for key in sorted(by_epoch)]
