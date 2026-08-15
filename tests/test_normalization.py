from datetime import datetime, timezone
from decimal import Decimal

from crypto_simulator.models import OHLCVBar
from crypto_simulator.normalization import deduplicate_bars


def bar(timestamp: int, close: str) -> OHLCVBar:
    return OHLCVBar("test", "BTC", "spot", datetime.fromtimestamp(timestamp, timezone.utc), "1", "2", "0.5", close, Decimal("1"))


def test_deduplicate_bars_is_sorted_and_last_write_wins() -> None:
    result = deduplicate_bars([bar(2, "3"), bar(1, "2"), bar(2, "4")])
    assert [item.epoch_ms for item in result] == [1000, 2000]
    assert result[-1].close == Decimal("4")
