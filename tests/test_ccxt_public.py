from datetime import datetime, timedelta, timezone

from crypto_simulator.adapters.ccxt_public import CcxtPublicAdapter


class FakeCcxtClient:
    has = {"fetchOHLCV": True}
    markets = {"BTC/JPY": {"spot": True}}

    def load_markets(self) -> None:
        return None

    def fetch_ohlcv(self, symbol: str, *, timeframe: str, since: int, limit: int) -> list[list[object]]:
        assert symbol == "BTC/JPY"
        assert timeframe == "1h"
        return [
            [since, "100", "101", "99", "100.5", "1"],
            [since + 3600000, "100.5", "102", "100", "101.5", "2"],
        ]


def test_ccxt_adapter_is_public_only_and_normalizes_candles() -> None:
    adapter = CcxtPublicAdapter("fake", client=FakeCcxtClient())
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = adapter.fetch_ohlcv("BTC/JPY", "1h", start=start, end=start + timedelta(hours=1, minutes=30))
    assert len(bars) == 2
    assert bars[0].exchange == "fake"
    assert bars[0].market_type == "spot"
    assert str(bars[1].close) == "101.5"
    assert not hasattr(adapter, "client")
    assert not hasattr(adapter, "create_order")
