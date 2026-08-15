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
    assert event["event_id"] == "bitbank:btc_jpy:1735696800000:sma_cross_1_2"
    assert event["action"] == "buy"
    assert event["price"] == "102"
