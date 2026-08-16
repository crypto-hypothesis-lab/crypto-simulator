from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from ..models import OHLCVBar
from .http import JsonHttpClient, PublicApiError


_SUPPORTED = {"1min", "5min", "15min", "30min", "1hour", "4hour", "8hour", "12hour", "1day", "1week", "1month"}


class BitbankAdapter:
    exchange = "bitbank"
    endpoint = "https://public.bitbank.cc"

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
            raise ValueError(f"unsupported bitbank interval: {interval}")
        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(days=7)).astimezone(timezone.utc)
        dates = self._request_dates(interval, start.date(), end.date())
        all_bars: dict[int, OHLCVBar] = {}
        for requested_date in dates:
            date_text = requested_date.strftime("%Y%m%d") if interval in {"1min", "5min", "15min", "30min", "1hour"} else requested_date.strftime("%Y")
            url = f"{self.endpoint}/{quote(symbol.lower())}/candlestick/{interval}/{date_text}"
            try:
                response = self.client.get(url)
            except PublicApiError as error:
                # Bitbank returns HTTP 404 for a year before a pair was listed
                # (and sometimes after it was delisted).  That is expected for
                # a broad universe; keep the available history instead of
                # discarding the whole symbol.  Other failures remain fatal so
                # a partial API outage cannot silently look like clean data.
                if "HTTP Error 404" in str(error):
                    continue
                raise
            if not isinstance(response, dict) or response.get("success") != 1:
                raise PublicApiError(f"bitbank returned an error for {symbol} {interval} {date_text}: {response}")
            data = response.get("data", {}) if isinstance(response, dict) else {}
            for candle in data.get("candlestick", []):
                for open_, high, low, close, volume, epoch_ms in candle.get("ohlcv", []):
                    bar = OHLCVBar(
                        exchange=self.exchange,
                        symbol=symbol.lower(),
                        market_type="spot",
                        timestamp=datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc),
                        open=open_,
                        high=high,
                        low=low,
                        close=close,
                        volume=volume,
                    )
                    if start <= bar.timestamp <= end:
                        all_bars[bar.epoch_ms] = bar
        return [all_bars[key] for key in sorted(all_bars)]

    @staticmethod
    def _request_dates(interval: str, start: date, end: date) -> list[date]:
        if interval in {"4hour", "8hour", "12hour", "1day", "1week", "1month"}:
            years = range(start.year, end.year + 1)
            return [date(year, 1, 1) for year in years]
        days = []
        cursor = start
        while cursor <= end:
            days.append(cursor)
            cursor += timedelta(days=1)
        return days
