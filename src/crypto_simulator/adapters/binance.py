from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from ..models import OHLCVBar
from .http import JsonHttpClient, PublicApiError


_INTERVALS = {
    "1min": "1m",
    "1m": "1m",
    "3min": "3m",
    "5min": "5m",
    "5m": "5m",
    "15min": "15m",
    "15m": "15m",
    "30min": "30m",
    "30m": "30m",
    "1hour": "1h",
    "1h": "1h",
    "2hour": "2h",
    "4hour": "4h",
    "4h": "4h",
    "6hour": "6h",
    "8hour": "8h",
    "12hour": "12h",
    "1day": "1d",
    "1d": "1d",
    "3day": "3d",
    "1week": "1w",
    "1month": "1M",
}

class BinanceAdapter:
    """Public Binance Spot klines adapter for efficient historical research.

    It uses the public market-data host and exposes no account, order, or
    withdrawal methods. Binance returns at most 1,000 klines per request, so a
    one-year 1-hour history requires roughly nine requests instead of one
    request per calendar day.
    """

    exchange = "binance"
    endpoint = "https://data-api.binance.vision/api/v3/klines"
    page_limit = 1000

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
        try:
            api_interval = _INTERVALS[interval]
        except KeyError as exc:
            raise ValueError(f"unsupported Binance interval: {interval}") from exc

        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(days=7)).astimezone(timezone.utc)
        if start > end:
            raise ValueError("start must not be later than end")
        api_symbol = symbol.replace("/", "").replace("_", "").replace("-", "").upper()
        cursor_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        bars: dict[int, OHLCVBar] = {}

        while cursor_ms <= end_ms:
            url = f"{self.endpoint}?{urlencode({'symbol': api_symbol, 'interval': api_interval, 'startTime': cursor_ms, 'endTime': end_ms, 'limit': self.page_limit})}"
            response = self.client.get(url)
            if not isinstance(response, list):
                raise PublicApiError(f"Binance returned an error for {api_symbol} {api_interval}: {response}")
            if not response:
                break
            for item in response:
                if not isinstance(item, list) or len(item) < 8:
                    raise PublicApiError(f"Binance returned an invalid kline: {item}")
                timestamp = datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc)
                if start <= timestamp <= end:
                    bar = OHLCVBar(
                        exchange=self.exchange,
                        symbol=symbol.upper(),
                        market_type="spot",
                        timestamp=timestamp,
                        open=item[1],
                        high=item[2],
                        low=item[3],
                        close=item[4],
                        volume=item[5],
                        quote_volume=item[7],
                    )
                    bars[bar.epoch_ms] = bar
            last_open_ms = int(response[-1][0])
            if last_open_ms < cursor_ms:
                raise PublicApiError("Binance returned a page that did not advance the cursor")
            # Advancing by one millisecond works for fixed and calendar-sized
            # intervals alike; the next response starts at the next open time.
            cursor_ms = last_open_ms + 1
            if len(response) < self.page_limit:
                break

        return [bars[key] for key in sorted(bars)]
