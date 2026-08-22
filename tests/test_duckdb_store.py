from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from crypto_simulator.derivatives import DerivativesObservation
from crypto_simulator.duckdb_store import DuckDbCandleStore, DuckDbDerivativesStore
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


def test_derivatives_duckdb_store_upserts_and_restores_history(tmp_path: Path) -> None:
    store = DuckDbDerivativesStore(tmp_path / "market.duckdb")
    observation = DerivativesObservation(
        venue="bybit",
        symbol="BTC",
        market_type="perpetual",
        instrument="BTCUSDT",
        observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        mark_price="100",
        open_interest_usd="1000",
        funding_rate="0.0008",
        funding_interval_hours="8",
        status="fresh",
        source="test",
    )
    assert store.upsert([observation]) == 1
    assert store.upsert([observation]) == 1
    loaded = store.load(symbol="btc", market_type="perpetual")
    assert len(loaded) == 1
    assert loaded[0].open_interest_usd == Decimal("1000")
    assert loaded[0].funding_interval_hours == Decimal("8")
    assert loaded[0].instrument == "BTCUSDT"
