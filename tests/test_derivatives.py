from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.adapters.derivatives import BybitDerivativesAdapter, HyperliquidDerivativesAdapter, OKXDerivativesAdapter
from crypto_simulator.derivatives import (
    DATA_STALE,
    DerivativesObservation,
    build_derivatives_features,
    build_derivatives_shadow_report,
    classify_derivatives_regime,
)


AS_OF = datetime(2025, 1, 2, tzinfo=timezone.utc)


def _observation(venue: str, timestamp: datetime, price: str, oi: str, funding: str = "0.0008") -> DerivativesObservation:
    return DerivativesObservation(
        venue=venue,
        symbol="BTC",
        market_type="perpetual",
        observed_at=timestamp,
        exchange_timestamp=timestamp,
        mark_price=price,
        index_price=price,
        open_interest_usd=oi,
        funding_rate=funding,
        funding_interval_hours="8",
        status="fresh",
        source="test",
    )


def _history() -> list[DerivativesObservation]:
    snapshots = {
        timedelta(hours=24): ("100", "100"),
        timedelta(hours=4): ("105", "105"),
        timedelta(hours=1): ("109", "110"),
        timedelta(0): ("110", "120"),
    }
    return [
        _observation(venue, AS_OF - offset, price, oi)
        for venue in ("hyperliquid", "bybit")
        for offset, (price, oi) in snapshots.items()
    ]


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url):
        self.calls.append(("GET", url))
        if isinstance(self.response, dict) and url.endswith("/market/ticker?instType=SWAP&instId=BTC-USDT-SWAP"):
            return self.response["ticker"]
        if isinstance(self.response, dict) and "/public/mark-price?" in url:
            return self.response["mark"]
        if isinstance(self.response, dict) and "/public/open-interest?" in url:
            return self.response["oi"]
        if isinstance(self.response, dict) and "/public/funding-rate?" in url:
            return self.response["funding"]
        return self.response

    def post(self, url, body):
        self.calls.append(("POST", url, body))
        return self.response


def test_observation_round_trip_keeps_decimal_and_utc() -> None:
    item = _observation("bybit", AS_OF, "100", "200")
    restored = DerivativesObservation.from_dict(item.to_dict())
    assert restored.open_interest_usd == Decimal("200")
    assert restored.effective_timestamp == AS_OF


def test_public_adapters_normalize_hyperliquid_bybit_and_okx() -> None:
    hyperliquid_client = FakeClient(
        [
            {"universe": [{"name": "BTC"}]},
            [{"markPx": "100", "oraclePx": "99", "openInterest": "10", "funding": "0.0001", "dayNtlVlm": "1000000"}],
        ]
    )
    hyperliquid = HyperliquidDerivativesAdapter(hyperliquid_client).fetch_snapshot("BTC", observed_at=AS_OF)
    assert hyperliquid.open_interest == Decimal("10")
    assert hyperliquid.funding_interval_hours == Decimal("1")

    bybit_client = FakeClient(
        {
            "retCode": 0,
            "time": "1735776000000",
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "markPrice": "100",
                        "indexPrice": "99",
                        "openInterest": "10",
                        "openInterestValue": "1000",
                        "fundingRate": "0.0008",
                        "fundingIntervalHour": "8",
                        "turnover24h": "1000000",
                    }
                ]
            },
        }
    )
    bybit = BybitDerivativesAdapter(bybit_client).fetch_snapshot("BTC/USDT", observed_at=AS_OF)
    assert bybit.symbol == "BTC"
    assert bybit.instrument == "BTCUSDT"
    assert bybit.open_interest_usd == Decimal("1000")
    assert bybit.funding_interval_hours == Decimal("8")
    assert "symbol=BTCUSDT" in bybit_client.calls[0][1]

    okx_client = FakeClient(
        {
            "ticker": {"code": "0", "data": [{"last": "100", "idxPx": "99", "ts": "1735776000000", "volCcy24h": "1000000"}]},
            "mark": {"code": "0", "data": [{"markPx": "100"}]},
            "oi": {"code": "0", "data": [{"oi": "10", "oiUsd": "1000"}]},
            "funding": {"code": "0", "data": [{"fundingRate": "0.0008", "fundingInterval": "28800000", "fundingTime": "1735776000000"}]},
        }
    )
    okx = OKXDerivativesAdapter(okx_client).fetch_snapshot("BTC-USDT-SWAP", observed_at=AS_OF)
    assert okx.symbol == "BTC"
    assert okx.instrument == "BTC-USDT-SWAP"
    assert okx.open_interest_usd == Decimal("1000")
    assert okx.funding_interval_hours == Decimal("8")
    assert len(okx_client.calls) == 4


def test_features_use_two_venue_median_and_classify_shadow_regime() -> None:
    features = build_derivatives_features(_history(), as_of=AS_OF)["BTC"]
    assert features.status == "fresh"
    assert features.fresh_venue_count == 2
    assert features.price_change_1h == Decimal("0.009174311926605504587155963")
    assert features.open_interest_change_1h == Decimal(str(Decimal("120") / Decimal("110") - 1))
    regime = classify_derivatives_regime(features)
    assert regime.label == "leveraged_long_expansion"
    assert regime.funding_state == "positive_crowding"
    assert regime.no_trade is False


def test_future_snapshot_is_not_used_and_stale_data_is_fail_closed() -> None:
    observations = _history() + [_observation("hyperliquid", AS_OF + timedelta(hours=1), "1000", "1000")]
    features = build_derivatives_features(observations, as_of=AS_OF)["BTC"]
    assert features.price_change_1h == Decimal("0.009174311926605504587155963")

    stale = [
        _observation("hyperliquid", AS_OF - timedelta(hours=4), "100", "100"),
        _observation("bybit", AS_OF - timedelta(hours=4), "100", "100"),
    ]
    stale_features = build_derivatives_features(stale, as_of=AS_OF)["BTC"]
    assert stale_features.status == DATA_STALE
    assert classify_derivatives_regime(stale_features).no_trade is True


def test_shadow_report_explicitly_does_not_change_canonical_strategy() -> None:
    report = build_derivatives_shadow_report(_history(), as_of=AS_OF)
    assert report["schema"] == "crypto.derivatives-shadow.v1"
    assert report["mode"] == "shadow"
    assert report["canonical_strategy_changed"] is False
    assert report["regimes"]["BTC"]["no_trade"] is False
