from datetime import datetime, timedelta, timezone
from decimal import Decimal

from crypto_simulator.limit_bracket import (
    LimitBracketSignal,
    LimitBracketSpec,
    build_limit_bracket_signal_event,
    default_mexc_event_specs,
    default_mexc_event_v2_specs,
    _build_timeframe_view,
    _LiveBracket,
    _MarketContext,
    _event_filter_passes,
    _limit_fill,
    _take_profit_fill,
    _update_excursion,
    _volatility_percentile,
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


def test_mexc_event_specs_are_explicit_and_short_horizon() -> None:
    short, long, rejection = default_mexc_event_specs("perpetual")
    assert short.strategy_family == "mexc_event_short"
    assert short.event_only is True
    assert short.required_regime == "risk_off"
    assert short.required_daily_direction == "RED"
    assert (short.min_consecutive_green_1h, short.max_consecutive_green_1h) == (3, 4)
    assert short.min_relative_return == 0.05
    assert short.min_funding_rate == -0.0005
    assert short.max_holding_hours == 8
    assert short.risk_per_trade == 0.001
    assert long.strategy_family == "mexc_event_long_pullback"
    assert long.required_regime == "risk_on"
    assert long.max_holding_hours == 8
    assert rejection.strategy_family == "mexc_event_short_rejection_volume"
    assert rejection.min_volume_multiple == 1.5
    assert rejection.require_rejection_candle is True
    assert rejection.min_rejection_fraction == 0.55
    assert (rejection.min_prior_consecutive_green_1h, rejection.max_prior_consecutive_green_1h) == (3, 6)
    assert rejection.max_gross_leverage == 5.0


def test_mexc_event_router_v2_has_distinct_lineage_and_permission_model() -> None:
    v1 = default_mexc_event_specs("perpetual")
    v2 = default_mexc_event_v2_specs("perpetual")
    assert [spec.name for spec in v1] != [spec.name for spec in v2]
    assert all(spec.regime_model == "legacy" for spec in v1)
    assert all(spec.regime_model == "router_v2" for spec in v2)
    assert all(spec.strategy_family.endswith("_router_v2") for spec in v2)
    assert all(spec.required_regime == baseline.required_regime for spec, baseline in zip(v2, v1))


def test_volatility_router_uses_only_trailing_history() -> None:
    closes = [Decimal("100")] * 120
    price = Decimal("100")
    for index in range(20):
        price *= Decimal("1.08") if index % 2 == 0 else Decimal("0.92")
        closes.append(price)
    realized, percentile = _volatility_percentile(closes, 20, 120)
    assert realized is not None and realized > 0
    assert percentile is not None and percentile > 0.90


def test_mae_mfe_are_recorded_in_initial_risk_units() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc)
    signal = LimitBracketSignal(
        symbol="BTC",
        direction="long",
        signal_index=0,
        signal_timestamp=timestamp.isoformat(),
        limit_price=Decimal("100"),
        atr=Decimal("5"),
        stop_distance=Decimal("5"),
        score=0.8,
        regime="risk_on",
        breadth=0.7,
        theme_score=0.8,
        breakout_level=Decimal("100"),
    )
    position = _LiveBracket(
        "BTC",
        "long",
        Decimal("1"),
        Decimal("100"),
        timestamp.isoformat(),
        0,
        Decimal("0"),
        Decimal("95"),
        Decimal("110"),
        signal,
    )
    _update_excursion(position, make_bar(timestamp, "100", "110", "90", "100"))
    assert position.max_favorable_r == 2.0
    assert position.max_adverse_r == 2.0


def test_rejection_volume_event_requires_causal_confirmation_features() -> None:
    spec = default_mexc_event_specs("perpetual")[2]
    context = _MarketContext("risk_off", 0.2)
    passing = {
        "daily_direction": "RED",
        "consecutive_green_1h": 0,
        "prior_consecutive_green_1h": 4,
        "volume_multiple": 1.8,
        "is_red_candle": True,
        "rejection_fraction": 0.7,
        "relative_return": 0.05,
        "funding_rate": Decimal("0"),
    }
    assert _event_filter_passes(context, spec, passing) is True
    assert _event_filter_passes(context, spec, {**passing, "volume_multiple": 1.2}) is False
    assert _event_filter_passes(context, spec, {**passing, "is_red_candle": False}) is False
    assert _event_filter_passes(context, spec, {**passing, "rejection_fraction": 0.4}) is False


def test_spec_rejects_unbounded_short_term_holding() -> None:
    try:
        LimitBracketSpec("too_short", max_holding_hours=0)
    except ValueError as exc:
        assert "720" in str(exc)
    else:
        raise AssertionError("zero-hour holding period should be rejected")


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
    assert event["signal_source"] == "crypto-simulator"
    assert event["strategy_id"] == "event"
    assert event["strategy_version"] == "event"
    assert event["decision"] == "no_trade"
    assert event["regime"]["label"] == "warmup"
    assert event["candidates"] == []
    assert event["no_trade_reason"] == "insufficient_higher_timeframe_history"
