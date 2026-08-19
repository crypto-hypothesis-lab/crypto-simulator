from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.limit_bracket import (
    LimitBracketSignal,
    LimitBracketSpec,
    build_limit_bracket_signal_event,
    _build_timeframe_view,
    _limit_fill,
    _take_profit_fill,
)
from crypto_simulator.models import OHLCVBar


def make_bar(timestamp: datetime, open_: str, high: str, low: str, close: str) -> OHLCVBar:
    return OHLCVBar("test", "BTC", "perpetual", timestamp, open_, high, low, close, "100")


def test_limit_entry_has_no_market_fallback_and_gap_gets_better_price() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert _limit_fill(make_bar(timestamp, "105", "110", "102", "108"), "long", Decimal("100")) is None
    assert _limit_fill(make_bar(timestamp, "95", "110", "90", "105"), "long", Decimal("100")) == Decimal("95")
    assert _limit_fill(make_bar(timestamp, "95", "110", "90", "105"), "short", Decimal("110")) == Decimal("110")


def test_take_profit_limit_gets_better_opening_gap() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bar = make_bar(timestamp, "120", "125", "115", "118")
    assert _take_profit_fill(bar, "long", Decimal("110")) == Decimal("120")
    assert _take_profit_fill(bar, "short", Decimal("110")) == Decimal("110")


def test_higher_timeframe_view_only_releases_completed_candles() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = [make_bar(start + timedelta(hours=index), "100", "101", "99", "100") for index in range(24)]
    view = _build_timeframe_view(bars, "1hour")
    assert view.four_hour.through(start + timedelta(hours=2)) == []
    assert len(view.four_hour.through(start + timedelta(hours=3))) == 1
    assert view.daily.through(start + timedelta(hours=22)) == []
    assert len(view.daily.through(start + timedelta(hours=23))) == 1


def test_spec_never_allows_more_than_one_month() -> None:
    try:
        LimitBracketSpec("too_long", max_holding_days=31)
    except ValueError as exc:
        assert "30" in str(exc)
    else:
        raise AssertionError("max holding period should be capped at 30 days")


def test_signal_event_is_safe_no_trade_snapshot_during_warmup() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    universe = {
        "BTC": [make_bar(start + timedelta(hours=index), "100", "101", "99", "100") for index in range(24)],
    }
    event = build_limit_bracket_signal_event(
        universe,
        LimitBracketSpec("event", market="spot", risk_off_shorts=False, max_gross_leverage=1, symbol_max_leverage=1),
        interval="1hour",
    )
    assert event["schema_version"] == "crypto.bracket-signal.v1"
    assert event["decision"] == "no_trade"
    assert event["regime"]["label"] == "warmup"
    assert event["candidates"] == []
    assert event["no_trade_reason"] == "insufficient_higher_timeframe_history"
