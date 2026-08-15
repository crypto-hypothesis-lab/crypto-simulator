from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from ..models import OHLCVBar
from .http import JsonHttpClient, PublicApiError


_SUPPORTED = {"1min", "5min", "10min", "15min", "30min", "1hour", "4hour", "8hour", "12hour", "1day", "1week", "1month"}


class GmoCoinAdapter:
    exchange = "gmo_coin"
    endpoint = "https://api.coin.z.com/public/v1/klines"

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
        if interval not in _SUPPORTED:
            raise ValueError(f"unsupported GMO Coin interval: {interval}")
        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(days=7)).astimezone(timezone.utc)
        all_bars: dict[int, OHLCVBar] = {}
        for requested_date in self._request_dates(interval, start.date(), end.date()):
            date_text = requested_date.strftime("%Y%m%d") if interval in {"1min", "5min", "10min", "15min", "30min", "1hour"} else requested_date.strftime("%Y")
            url = f"{self.endpoint}?{urlencode({'symbol': symbol.upper(), 'interval': interval, 'date': date_text})}"
            response = self.client.get(url)
            if not isinstance(response, dict) or response.get("status") != 0:
                raise PublicApiError(f"GMO Coin returned an error for {symbol} {interval} {date_text}: {response}")
            for item in response.get("data", []):
                bar = OHLCVBar(
                    exchange=self.exchange,
                    symbol=symbol.upper(),
                    market_type="spot",
                    timestamp=datetime.fromtimestamp(int(item["openTime"]) / 1000, tz=timezone.utc),
                    open=item["open"],
                    high=item["high"],
                    low=item["low"],
                    close=item["close"],
                    volume=item["volume"],
                )
                if start <= bar.timestamp <= end:
                    all_bars[bar.epoch_ms] = bar
        return [all_bars[key] for key in sorted(all_bars)]

    @staticmethod
    def _request_dates(interval: str, start: date, end: date) -> list[date]:
        if interval in {"4hour", "8hour", "12hour", "1day", "1week", "1month"}:
            return [date(year, 1, 1) for year in range(start.year, end.year + 1)]
        days = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
        return days
