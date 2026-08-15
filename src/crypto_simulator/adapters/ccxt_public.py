from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..models import OHLCVBar
from .http import PublicApiError


_TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}


class CcxtPublicAdapter:
    """Read-only CCXT adapter that never receives API credentials."""

    def __init__(self, exchange_id: str, *, client: object | None = None) -> None:
        self.exchange = exchange_id
        if client is not None:
            self._client = client
            return
        try:
            import ccxt
        except ImportError as exc:
            raise RuntimeError("Install crypto-simulator[exchanges] to use the CCXT adapter") from exc
        try:
            exchange_factory = getattr(ccxt, exchange_id)
        except AttributeError as exc:
            raise ValueError(f"unsupported CCXT exchange id: {exchange_id}") from exc
        self._client = exchange_factory({"enableRateLimit": True})

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(days=7)).astimezone(timezone.utc)
        if start >= end:
            raise ValueError("start must be before end")
        if not getattr(self._client, "has", {}).get("fetchOHLCV"):
            raise PublicApiError(f"CCXT exchange {self.exchange} does not support public OHLCV")

        self._client.load_markets()
        market = getattr(self._client, "markets", {}).get(symbol, {})
        market_type = "perpetual" if market.get("swap") or market.get("future") else "spot"
        timeframe_seconds = self._timeframe_seconds(interval)
        step_ms = timeframe_seconds * 1000
        cursor_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bars: dict[int, OHLCVBar] = {}

        page_count = 0
        while cursor_ms <= end_ms:
            page_count += 1
            if page_count > 1000:
                raise PublicApiError(f"CCXT pagination exceeded safety limit for {self.exchange} {symbol}")
            rows = self._client.fetch_ohlcv(symbol, timeframe=interval, since=cursor_ms, limit=1000)
            if not rows:
                break
            for row in rows:
                if len(row) < 6:
                    continue
                timestamp_ms, open_, high, low, close, volume = row[:6]
                timestamp_ms = int(timestamp_ms)
                if not int(start.timestamp() * 1000) <= timestamp_ms <= end_ms:
                    continue
                bar = OHLCVBar(
                    exchange=self.exchange,
                    symbol=symbol,
                    market_type=market_type,
                    timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
                bars[bar.epoch_ms] = bar
            last_timestamp_ms = max(int(row[0]) for row in rows if row)
            next_cursor_ms = last_timestamp_ms + step_ms
            if next_cursor_ms <= cursor_ms:
                break
            cursor_ms = next_cursor_ms
        return [bars[key] for key in sorted(bars)]

    def _timeframe_seconds(self, interval: str) -> int:
        if interval in _TIMEFRAME_SECONDS:
            return _TIMEFRAME_SECONDS[interval]
        parse_timeframe = getattr(self._client, "parse_timeframe", None)
        if parse_timeframe is None:
            raise ValueError(f"unsupported CCXT timeframe: {interval}")
        seconds = int(parse_timeframe(interval))
        if seconds <= 0:
            raise ValueError(f"unsupported CCXT timeframe: {interval}")
        return seconds
