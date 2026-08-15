from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from crypto_simulator.dataset import load_ohlcv_csv, merge_ohlcv_csv
from crypto_simulator.models import OHLCVBar


def make_bar(hour: int, close: str) -> OHLCVBar:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    return OHLCVBar("bitbank", "btc_jpy", "spot", timestamp, close, close, close, close, Decimal("1"))


def test_merge_is_sorted_deduplicated_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "data.csv"
    added, total = merge_ohlcv_csv(path, [make_bar(1, "101"), make_bar(0, "100")])
    assert (added, total) == (2, 2)

    added, total = merge_ohlcv_csv(path, [make_bar(1, "102"), make_bar(2, "103")])
    assert (added, total) == (1, 3)
    bars = load_ohlcv_csv(path)
    assert [bar.close for bar in bars] == [Decimal("100"), Decimal("102"), Decimal("103")]
