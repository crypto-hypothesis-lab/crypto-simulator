from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import OHLCVBar
from ..portfolio import FundingPoint
from .http import JsonHttpClient


_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,
}


class HyperliquidAdapter:
    exchange = "hyperliquid"
    endpoint = "https://api.hyperliquid.xyz/info"

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient()

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        if interval not in _INTERVAL_MS:
            raise ValueError(f"unsupported Hyperliquid interval: {interval}")
        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(milliseconds=_INTERVAL_MS[interval] * 5000)).astimezone(timezone.utc)
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": symbol.upper(),
                "interval": interval,
                "startTime": int(start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
            },
        }
        response = self.client.post(self.endpoint, payload)
        if not isinstance(response, list):
            raise ValueError("unexpected Hyperliquid candle response")
        bars = []
        for item in response:
            bars.append(
                OHLCVBar(
                    exchange=self.exchange,
                    symbol=symbol.upper(),
                    market_type="perpetual",
                    timestamp=datetime.fromtimestamp(int(item["t"]) / 1000, tz=timezone.utc),
                    open=item["o"],
                    high=item["h"],
                    low=item["l"],
                    close=item["c"],
                    volume=item["v"],
                )
            )
        return sorted(bars, key=lambda bar: bar.timestamp)

    def fetch_funding(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime | None = None,
    ) -> list[FundingPoint]:
        """Fetch public hourly perpetual funding with pagination.

        HyperLiquid returns a bounded time-range response. Advancing from the
        last returned timestamp keeps long research windows complete without
        relying on private account data.
        """

        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        cursor = start.astimezone(timezone.utc)
        if cursor >= end:
            raise ValueError("start must be earlier than end")
        points: dict[int, FundingPoint] = {}
        while cursor < end:
            payload = {
                "type": "fundingHistory",
                "coin": symbol.upper(),
                "startTime": int(cursor.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
            }
            response = self.client.post(self.endpoint, payload)
            if not isinstance(response, list):
                raise ValueError("unexpected Hyperliquid funding response")
            for item in response:
                timestamp = datetime.fromtimestamp(int(item["time"]) / 1000, tz=timezone.utc)
                if start <= timestamp <= end:
                    point = FundingPoint(
                        exchange=self.exchange,
                        symbol=symbol.upper(),
                        timestamp=timestamp,
                        rate=item["fundingRate"],
                        premium=item.get("premium"),
                    )
                    points[point.timestamp_ms] = point
            if len(response) < 500:
                break
            last_timestamp = max(int(item["time"]) for item in response)
            if last_timestamp < int(cursor.timestamp() * 1000):
                break
            cursor = datetime.fromtimestamp((last_timestamp + 1) / 1000, tz=timezone.utc)
        return [points[key] for key in sorted(points)]
