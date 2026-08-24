"""Common, immutable market features shared by parallel strategies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Mapping

from .models import OHLCVBar
from .timeframes import interval_duration


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _decimal_text(value: object) -> str:
    return str(value)


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _atr(bars: list[OHLCVBar], window: int = 14) -> float | None:
    if len(bars) < window + 1:
        return None
    true_ranges: list[float] = []
    for previous, current in zip(bars[-(window + 1):-1], bars[-window:]):
        true_ranges.append(max(
            float(current.high - current.low),
            abs(float(current.high - previous.close)),
            abs(float(current.low - previous.close)),
        ))
    return sum(true_ranges) / window


def _volume_zscore(bars: list[OHLCVBar], window: int = 20) -> float | None:
    if len(bars) < window:
        return None
    values = [float(bar.volume) for bar in bars[-window:]]
    mean = sum(values) / window
    variance = sum((value - mean) ** 2 for value in values) / window
    standard_deviation = variance ** 0.5
    return 0.0 if standard_deviation == 0 else (values[-1] - mean) / standard_deviation


def _missing_bars(bars: list[OHLCVBar], duration: timedelta) -> int:
    if len(bars) < 2:
        return 0
    expected_seconds = int(duration.total_seconds())
    missing = 0
    for previous, current in zip(bars, bars[1:]):
        gap = int((current.timestamp - previous.timestamp).total_seconds())
        if gap > expected_seconds:
            missing += max(0, gap // expected_seconds - 1)
    return missing


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    schema_version: str
    venue: str
    symbol: str
    market_type: str
    interval: str
    candle_close_at: datetime
    bars_hash: str
    data_quality: str
    missing_bars: int
    features: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "candle_close_at", self.candle_close_at.astimezone(timezone.utc))
        object.__setattr__(self, "created_at", self.created_at.astimezone(timezone.utc))
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "venue": self.venue,
            "symbol": self.symbol,
            "market_type": self.market_type,
            "interval": self.interval,
            "candle_close_at": self.candle_close_at.isoformat(),
            "bars_hash": self.bars_hash,
            "data_quality": self.data_quality,
            "missing_bars": self.missing_bars,
            "features": dict(self.features),
            "created_at": self.created_at.isoformat(),
        }


def build_feature_snapshot(
    bars: list[OHLCVBar],
    *,
    interval: str,
    as_of: datetime | None = None,
    max_age: timedelta = timedelta(hours=2),
) -> FeatureSnapshot:
    if not bars:
        raise ValueError("bars must not be empty")
    duration = interval_duration(interval)
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    identities = {(bar.exchange, bar.symbol, bar.market_type) for bar in ordered}
    if len(identities) != 1:
        raise ValueError("all bars must belong to one exchange, symbol, and market type")
    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    closed = [bar for bar in ordered if bar.timestamp + duration <= now]
    if not closed:
        raise ValueError("no closed bars available")
    latest = closed[-1]
    missing_bars = _missing_bars(closed, duration)
    age = now - (latest.timestamp + duration)
    data_quality = "fresh" if timedelta(0) <= age <= max_age and missing_bars == 0 else "degraded"
    closes = [float(bar.close) for bar in closed]
    features: dict[str, Any] = {
        "close": _decimal_text(latest.close),
        "volume": _decimal_text(latest.volume),
        "sma_12": _sma(closes, 12),
        "sma_24": _sma(closes, 24),
        "atr": _atr(closed),
        "volume_zscore": _volume_zscore(closed),
        "return_24h": (closes[-1] / closes[-25] - 1) if len(closes) >= 25 and closes[-25] else None,
    }
    sma12 = features["sma_12"]
    sma24 = features["sma_24"]
    features["regime"] = "risk_on" if sma12 is not None and sma24 is not None and sma12 > sma24 else "risk_off" if sma12 is not None and sma24 is not None else "unknown"
    bar_payload = [bar.to_dict() for bar in closed]
    bars_hash = sha256(_canonical(bar_payload).encode("utf-8")).hexdigest()
    exchange, symbol, market_type = next(iter(identities))
    return FeatureSnapshot(
        schema_version="crypto.feature-snapshot.v1",
        venue=exchange,
        symbol=symbol,
        market_type=market_type,
        interval=interval,
        candle_close_at=latest.timestamp + duration,
        bars_hash=bars_hash,
        data_quality=data_quality,
        missing_bars=missing_bars,
        features=features,
        created_at=now,
    )
