from datetime import datetime, timezone

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


def test_hyperliquid_candle_shape_is_normalized() -> None:
    client = FakeClient([{"t": 1_700_000_000_000, "o": "1", "h": "2", "l": "0.5", "c": "1.5", "v": "3"}])
    bars = HyperliquidAdapter(client).fetch_ohlcv("BTC", "1h")
    assert len(bars) == 1
    assert bars[0].market_type == "perpetual"
    assert client.urls[0][1]["type"] == "candleSnapshot"


def test_bitbank_candle_shape_is_normalized() -> None:
    client = FakeClient({"success": 1, "data": {"candlestick": [{"type": "1hour", "ohlcv": [["1", "2", "0.5", "1.5", "3", 1_700_000_000_000]]}]}})
    bars = BitbankAdapter(client).fetch_ohlcv("btc_jpy", "1hour", start=datetime.fromtimestamp(1_699_999_000, timezone.utc), end=datetime.fromtimestamp(1_700_001_000, timezone.utc))
    assert bars[0].symbol == "btc_jpy"
    assert bars[0].market_type == "spot"


def test_gmo_maintenance_is_fail_closed() -> None:
    client = FakeClient({"status": 5, "messages": [{"message_string": "MAINTENANCE"}]})
    try:
        GmoCoinAdapter(client).fetch_ohlcv("BTC", "1hour")
    except PublicApiError as error:
        assert "MAINTENANCE" in str(error)
    else:
        raise AssertionError("maintenance response must fail closed")
