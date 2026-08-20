from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlencode

from ..models import OHLCVBar
from ..portfolio import FundingPoint
from .http import JsonHttpClient, PublicApiError


_INTERVALS = {
    "1m": ("Min1", 60),
    "5m": ("Min5", 5 * 60),
    "15m": ("Min15", 15 * 60),
    "30m": ("Min30", 30 * 60),
    "1h": ("Min60", 60 * 60),
    "1hour": ("Min60", 60 * 60),
    "4h": ("Hour4", 4 * 60 * 60),
    "4hour": ("Hour4", 4 * 60 * 60),
    "8h": ("Hour8", 8 * 60 * 60),
    "1d": ("Day1", 24 * 60 * 60),
    "1day": ("Day1", 24 * 60 * 60),
    "1w": ("Week1", 7 * 24 * 60 * 60),
    "1week": ("Week1", 7 * 24 * 60 * 60),
    "1M": ("Month1", 30 * 24 * 60 * 60),
    "1month": ("Month1", 30 * 24 * 60 * 60),
}


@dataclass(frozen=True, slots=True)
class MexcTicker:
    """Public MEXC perpetual ticker snapshot used only for liquidity selection."""

    symbol: str
    last_price: Decimal
    bid: Decimal
    ask: Decimal
    amount_24h: Decimal
    volume_24h: Decimal
    hold_volume: Decimal
    timestamp: datetime

    @property
    def spread_bps(self) -> Decimal:
        if self.last_price <= 0 or self.ask < self.bid:
            return Decimal("999999")
        return (self.ask - self.bid) / self.last_price * Decimal("10000")


@dataclass(frozen=True, slots=True)
class MexcContractDetail:
    symbol: str
    base_coin: str
    base_coin_name: str
    quote_coin: str
    settle_coin: str
    state: int
    hidden: bool
    concept_plates: tuple[str, ...]

    @property
    def is_crypto_perpetual(self) -> bool:
        non_crypto_markers = ("stock", "etf", "metals", "commodity", "tradfi", "forex", "stockindex")
        plates = " ".join(self.concept_plates).lower()
        return self.quote_coin == "USDT" and self.settle_coin == "USDT" and not any(marker in plates for marker in non_crypto_markers)


class MexcContractAdapter:
    """Read-only MEXC perpetual market-data adapter.

    MEXC contract candles are returned as parallel arrays and the public API
    caps one response at 2,000 bars. The adapter chunks the requested window
    explicitly, so long research windows do not silently truncate.
    """

    exchange = "mexc"
    endpoint = "https://contract.mexc.com/api/v1/contract/kline"
    ticker_endpoint = "https://contract.mexc.com/api/v1/contract/ticker"
    detail_endpoint = "https://contract.mexc.com/api/v1/contract/detail"
    funding_endpoint = "https://contract.mexc.com/api/v1/contract/funding_rate/history"
    page_limit = 2000

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient(user_agent="crypto-simulator/mexc-contract")

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        value = symbol.upper().replace("/", "_").replace("-", "_")
        if "_" not in value and value.endswith("USDT"):
            value = f"{value[:-4]}_USDT"
        return value

    @staticmethod
    def _interval(interval: str) -> tuple[str, int]:
        try:
            return _INTERVALS[interval]
        except KeyError as exc:
            raise ValueError(f"unsupported MEXC contract interval: {interval}") from exc

    def fetch_tickers(self) -> list[MexcTicker]:
        """Fetch all public perpetual ticker snapshots for liquidity ranking."""

        response = self.client.get(self.ticker_endpoint)
        if not isinstance(response, dict) or response.get("success") is not True:
            raise PublicApiError(f"MEXC ticker returned an error: {response}")
        raw_data = response.get("data")
        items = raw_data if isinstance(raw_data, list) else [raw_data]
        tickers: list[MexcTicker] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            try:
                timestamp = datetime.fromtimestamp(int(item.get("timestamp") or 0) / 1000, tz=timezone.utc)
                ticker = MexcTicker(
                    symbol=self._normalise_symbol(str(item["symbol"])),
                    last_price=Decimal(str(item.get("lastPrice", "0"))),
                    bid=Decimal(str(item.get("bid1", "0"))),
                    ask=Decimal(str(item.get("ask1", "0"))),
                    amount_24h=Decimal(str(item.get("amount24", "0"))),
                    volume_24h=Decimal(str(item.get("volume24", "0"))),
                    hold_volume=Decimal(str(item.get("holdVol", "0"))),
                    timestamp=timestamp,
                )
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                raise PublicApiError(f"MEXC ticker returned invalid data: {item}") from exc
            if ticker.last_price > 0 and ticker.amount_24h >= 0 and ticker.timestamp > datetime.fromtimestamp(0, tz=timezone.utc):
                tickers.append(ticker)
        return tickers

    def fetch_contract_details(self) -> list[MexcContractDetail]:
        response = self.client.get(self.detail_endpoint)
        if not isinstance(response, dict) or response.get("success") is not True:
            raise PublicApiError(f"MEXC contract detail returned an error: {response}")
        raw_data = response.get("data")
        if not isinstance(raw_data, list):
            raise PublicApiError(f"MEXC contract detail returned invalid data: {response}")
        details: list[MexcContractDetail] = []
        for item in raw_data:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            details.append(
                MexcContractDetail(
                    symbol=self._normalise_symbol(str(item["symbol"])),
                    base_coin=str(item.get("baseCoin", "")),
                    base_coin_name=str(item.get("baseCoinName", item.get("baseCoin", ""))),
                    quote_coin=str(item.get("quoteCoin", "")),
                    settle_coin=str(item.get("settleCoin", "")),
                    state=int(item.get("state", 0)),
                    hidden=bool(item.get("isHidden", False)),
                    concept_plates=tuple(str(value) for value in (item.get("conceptPlate") or [])),
                )
            )
        return details

    def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        api_interval, interval_seconds = self._interval(interval)
        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = (start or end - timedelta(days=7)).astimezone(timezone.utc)
        if start > end:
            raise ValueError("start must not be later than end")

        api_symbol = self._normalise_symbol(symbol)
        start_seconds = int(start.timestamp())
        end_seconds = int(end.timestamp())
        cursor = start_seconds
        bars: dict[int, OHLCVBar] = {}
        chunk_seconds = interval_seconds * (self.page_limit - 1)

        while cursor <= end_seconds:
            chunk_end = min(end_seconds, cursor + chunk_seconds)
            query = urlencode({"interval": api_interval, "start": cursor, "end": chunk_end})
            response = self.client.get(f"{self.endpoint}/{api_symbol}?{query}")
            if not isinstance(response, dict) or response.get("success") is not True:
                raise PublicApiError(f"MEXC contract returned an error for {api_symbol}: {response}")
            data = response.get("data")
            if not isinstance(data, dict):
                raise PublicApiError(f"MEXC contract returned invalid kline data: {response}")
            times = data.get("time", [])
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("vol", [])
            amounts = data.get("amount")
            if amounts is None:
                amounts = [None] * len(times) if isinstance(times, list) else []
            arrays = (times, opens, highs, lows, closes, volumes, amounts)
            if not all(isinstance(value, list) for value in arrays):
                raise PublicApiError(f"MEXC contract returned non-list kline arrays: {response}")
            lengths = {len(value) for value in arrays}
            if len(lengths) != 1:
                raise PublicApiError(f"MEXC contract returned mismatched kline arrays: {response}")

            for timestamp, open_, high, low, close, volume, amount in zip(times, opens, highs, lows, closes, volumes, amounts):
                timestamp = int(timestamp)
                if not start_seconds <= timestamp <= end_seconds:
                    continue
                bar = OHLCVBar(
                    exchange=self.exchange,
                    symbol=api_symbol,
                    market_type="perpetual",
                    timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    quote_volume=amount,
                )
                bars[bar.epoch_ms] = bar

            if not times:
                break
            last_timestamp = max(int(value) for value in times)
            next_cursor = last_timestamp + interval_seconds
            if next_cursor <= cursor:
                raise PublicApiError("MEXC contract returned a page that did not advance the cursor")
            cursor = next_cursor

        return [bars[key] for key in sorted(bars)]

    def fetch_funding(
        self,
        symbol: str,
        *,
        start: datetime,
        end: datetime | None = None,
    ) -> list[FundingPoint]:
        """Fetch public historical funding settlements for a contract."""

        end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc)
        start = start.astimezone(timezone.utc)
        if start >= end:
            raise ValueError("start must be earlier than end")

        api_symbol = self._normalise_symbol(symbol)
        first_page = self.client.get(
            f"{self.funding_endpoint}?{urlencode({'symbol': api_symbol, 'page_num': 1, 'page_size': 1000})}"
        )
        if not isinstance(first_page, dict) or first_page.get("success") is not True:
            raise PublicApiError(f"MEXC funding returned an error for {api_symbol}: {first_page}")
        data = first_page.get("data")
        if not isinstance(data, dict):
            raise PublicApiError(f"MEXC funding returned invalid data: {first_page}")

        total_page = int(data.get("totalPage") or 1)
        rows = list(data.get("resultList") or [])
        for page in range(2, total_page + 1):
            response = self.client.get(
                f"{self.funding_endpoint}?{urlencode({'symbol': api_symbol, 'page_num': page, 'page_size': 1000})}"
            )
            if not isinstance(response, dict) or response.get("success") is not True:
                raise PublicApiError(f"MEXC funding returned an error on page {page}: {response}")
            page_data = response.get("data")
            if not isinstance(page_data, dict):
                raise PublicApiError(f"MEXC funding returned invalid page data: {response}")
            rows.extend(page_data.get("resultList") or [])

        points: dict[int, FundingPoint] = {}
        for item in rows:
            timestamp = datetime.fromtimestamp(int(item["settleTime"]) / 1000, tz=timezone.utc)
            if not start <= timestamp <= end:
                continue
            point = FundingPoint(
                exchange=self.exchange,
                symbol=api_symbol,
                timestamp=timestamp,
                rate=item["fundingRate"],
            )
            points[point.timestamp_ms] = point
        return [points[key] for key in sorted(points)]
