from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ..models import OHLCVBar


class MarketDataAdapter(Protocol):
    exchange: str

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[OHLCVBar]: ...
