from datetime import datetime, timezone
from decimal import Decimal

from crypto_simulator.adapters.binance import BinanceAdapter
from crypto_simulator.adapters.bitbank import BitbankAdapter
from crypto_simulator.adapters.gmo_coin import GmoCoinAdapter
from crypto_simulator.adapters.hyperliquid import HyperliquidAdapter
from crypto_simulator.adapters.http import PublicApiError


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.response

    def post(self, url, body):
        self.urls.append((url, body))
        return self.response


class MissingYearClient(FakeClient):
    def get(self, url):
        self.urls.append(url)
        if url.endswith("/2023"):
            raise PublicApiError("public API request failed: GET url: HTTP Error 404: Not Found")
        return {"success": 1, "data": {"candlestick": [{"type": "1day", "ohlcv": [["1", "2", "0.5", "1.5", "3", 1_700_000_000_000]]}]}}


def test_hyperliquid_candle_shape_is_normalized() -> None:
    client = FakeClient([{"t": 1_700_000_000_000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "3"}])
    bars = HyperliquidAdapter(client).fetch_ohlcv("BTC", "1h")
    assert len(bars) == 1
    assert bars[0].market_type == "perpetual"
    assert client.urls[0][1]["type"] == "candleSnapshot"


def test_hyperliquid_funding_shape_is_normalized() -> None:
    client = FakeClient(
        [
            {"coin": "BTC", "fundingRate": "0.0001", "premium": "0.0002", "time": 1_700_000_000_000},
        ]
    )
    start = datetime.fromtimestamp(1_699_999_000, timezone.utc)
    end = datetime.fromtimestamp(1_700_001_000, timezone.utc)
    points = HyperliquidAdapter(client).fetch_funding("BTC", start=start, end=end)

    assert len(points) == 1
    assert points[0].symbol == "BTC"
    assert points[0].rate == Decimal("0.0001")
    assert client.urls[0][1]["type"] == "fundingHistory"


def test_binance_kline_shape_is_normalized() -> None:
    timestamp = 1_700_000_000_000
    client = FakeClient([[timestamp, "1", "2", "0.5", "1.5", "3", timestamp + 3_599_999, "4.5", 10, "2", "3", "0"]])
    start = datetime.fromtimestamp(timestamp / 1000, timezone.utc)
    bars = BinanceAdapter(client).fetch_ohlcv("BTC/USDT", "1hour", start=start, end=start)

    assert len(bars) == 1
    assert bars[0].exchange == "binance"
    assert bars[0].symbol == "BTC/USDT"
    assert bars[0].quote_volume == 4.5
    assert "symbol=BTCUSDT" in client.urls[0]


def test_bitbank_candle_shape_is_normalized() -> None:
    client = FakeClient({"success": 1, "data": {"candlestick": [{"type": "1hour", "ohlcv": [["1", "2", "0.5", "1.5", "3", 1_700_000_000_000]]}]}})
    bars = BitbankAdapter(client).fetch_ohlcv("btc_jpy", "1hour", start=datetime.fromtimestamp(1_699_999_000, timezone.utc), end=datetime.fromtimestamp(1_700_001_000, timezone.utc))
    assert bars[0].symbol == "btc_jpy"
    assert bars[0].market_type == "spot"


def test_bitbank_skips_unlisted_years_but_keeps_available_history() -> None:
    client = MissingYearClient(None)
    bars = BitbankAdapter(client).fetch_ohlcv(
        "sol_jpy",
        "1day",
        start=datetime(2023, 1, 1, tzinfo=timezone.utc),
        end=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    assert len(bars) == 1
    assert any(url.endswith("/2023") for url in client.urls)
    assert any(url.endswith("/2024") for url in client.urls)


def test_gmo_maintenance_is_fail_closed() -> None:
    client = FakeClient({"status": 5, "messages": [{"message_string": "MAINTENANCE"}]})
    try:
        GmoCoinAdapter(client).fetch_ohlcv("BTC", "1hour")
    except PublicApiError as error:
        assert "MAINTENANCE" in str(error)
    else:
        raise AssertionError("maintenance response must fail closed")
