from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.derivatives import DerivativesObservation
from crypto_simulator.market_structure import build_market_structure_event_study
from crypto_simulator.models import OHLCVBar


def test_liquidation_proxy_reclaim_is_point_in_time_and_never_paper_authority() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    observations = []
    for index in range(62):
        timestamp = start + timedelta(hours=index)
        if index == 47:
            open_, close, volume = Decimal("100"), Decimal("94"), Decimal("250")
        elif index == 48:
            open_, close, volume = Decimal("93"), Decimal("95"), Decimal("300")
        else:
            open_ = close = Decimal("100") if index < 47 else Decimal("95")
            volume = Decimal("100")
        bars.append(
            OHLCVBar(
                "mexc",
                "BTC_USDT",
                "perpetual",
                timestamp,
                open_,
                max(open_, close) + Decimal("1"),
                min(open_, close) - Decimal("1"),
                close,
                volume,
                volume * close,
            )
        )
        oi = Decimal("1000") if index <= 44 else Decimal("900")
        observations.append(
            DerivativesObservation(
                venue="mexc",
                symbol="BTC_USDT",
                market_type="perpetual",
                observed_at=timestamp,
                exchange_timestamp=timestamp,
                mark_price=close,
                open_interest_usd=oi,
                funding_rate="0.00001",
                funding_interval_hours="1",
                status="fresh",
                source="test",
            )
        )

    report = build_market_structure_event_study({"BTC_USDT": bars}, observations, min_venues=1)
    events = [item for item in report["events"] if item["strategy_id"] == "mexc_long_liq_exhaustion_reclaim_v1"]
    assert len(events) == 1
    assert events[0]["event_timestamp"] == (start + timedelta(hours=48)).isoformat()
    assert events[0]["event_kind"] == "liquidation_proxy_reclaim"
    assert events[0]["features"]["oi_change_4h"] < 0
    assert report["mode"] == "event_study_only"
    assert report["paper_eligible"] is False
    assert report["summary"]["promotion_decision"] == "hold"


def test_missing_derivatives_is_recorded_as_blocked_instead_of_guessed() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = [
        OHLCVBar("mexc", "BTC_USDT", "perpetual", start + timedelta(hours=index), "100", "101", "99", "100", "100")
        for index in range(60)
    ]
    report = build_market_structure_event_study({"BTC_USDT": bars}, [])
    assert report["events"] == []
    assert report["blocked_observations"]["missing_derivatives"] > 0

