from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


@dataclass(frozen=True)
class OHLCVBar:
    """One exchange-native candle normalized to UTC."""

    exchange: str
    symbol: str
    market_type: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None

    def __post_init__(self) -> None:
        timestamp = self.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        object.__setattr__(self, "timestamp", timestamp)
        for name in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, name, _decimal(getattr(self, name)))
        if self.quote_volume is not None:
            object.__setattr__(self, "quote_volume", _decimal(self.quote_volume))
        if self.low > self.high:
            raise ValueError("low must not exceed high")
        if self.volume < 0:
            raise ValueError("volume must not be negative")

    @property
    def epoch_ms(self) -> int:
        return int(self.timestamp.timestamp() * 1000)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        for key in ("open", "high", "low", "close", "volume", "quote_volume"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data
