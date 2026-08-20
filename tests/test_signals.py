from datetime import datetime, timedelta, timezone
from crypto_simulator.models import OHLCVBar
from crypto_simulator.signals import build_signal_event, closed_bars


def make_bars(count: int) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [
        OHLCVBar("bitbank", "btc_jpy", "spot", start + timedelta(hours=index), str(100 + index), str(100 + index), str(100 + index), str(100 + index), "1")
        for index in range(count)
    ]


def test_closed_bars_excludes_current_candle() -> None:
    bars = make_bars(3)
    as_of = bars[-1].timestamp + timedelta(minutes=30)
    assert len(closed_bars(bars, "1hour", as_of=as_of)) == 2


def test_signal_event_is_deterministic_and_paper_ready() -> None:
    bars = make_bars(3)
    event = build_signal_event(bars, interval="1hour", fast_window=1, slow_window=2, as_of=bars[-1].timestamp + timedelta(hours=1))
    assert event["schema_version"] == "crypto.signal.v1"
    assert event["strategy_version"] == "sma_cross_1_2"
    assert event["candle_close_at"] == "2025-01-01T03:00:00+00:00"
    assert event["event_id"] == event["signal_key"]
    assert event["event_id"].endswith(":sma_cross_1_2:buy")
    assert event["action"] == "buy"
    assert event["price"] == "102"


def test_multi_timeframe_signal_uses_next_hour_open_and_exposes_layers() -> None:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = [
        OHLCVBar(
            "bitbank",
            "btc_jpy",
            "spot",
            start + timedelta(hours=index),
            str(100 + index),
            str(100 + index),
            str(100 + index),
            str(100 + index),
            "1",
        )
        for index in range(74)
    ]
    bars[-1] = OHLCVBar("bitbank", "btc_jpy", "spot", bars[-1].timestamp, "250", "250", "250", "250", "1")
    as_of = bars[-1].timestamp
    event = build_signal_event(
        bars,
        interval="1hour",
        fast_window=2,
        slow_window=3,
        trend_fast_window=2,
        trend_slow_window=3,
        regime_fast_window=2,
        regime_slow_window=3,
        multi_timeframe=True,
        as_of=as_of,
    )

    assert event["action"] == "buy"
    assert event["decision_price"] == "172"
    assert event["execution_price"] == "250"
    assert event["timestamp"] == "2025-01-04T01:00:00+00:00"
    assert event["price"] == event["execution_price"]
    assert event["execution_price_source"] == "next_candle_open"
    assert event["timeframes"]["trend"]["interval"] == "4hour"
    assert event["timeframes"]["regime"]["interval"] == "1day"
