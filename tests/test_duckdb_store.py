from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from crypto_simulator.duckdb_store import DuckDbCandleStore
from crypto_simulator.models import OHLCVBar


def test_duckdb_store_upserts_and_filters(tmp_path: Path) -> None:
    store = DuckDbCandleStore(tmp_path / "market.duckdb")
    bars = [
        OHLCVBar("bitbank", "btc_jpy", "spot", datetime(2025, 1, 1, tzinfo=timezone.utc), "100", "101", "99", "100", "1"),
        OHLCVBar("bitbank", "btc_jpy", "spot", datetime(2025, 1, 1, 1, tzinfo=timezone.utc), "100", "102", "99", "101", "2"),
    ]
    assert store.upsert(bars) == 2
    assert store.upsert([bars[0]]) == 1
    loaded = store.load(exchange="bitbank", symbol="btc_jpy", market_type="spot")
    assert len(loaded) == 2
    assert loaded[-1].close == Decimal("101")
