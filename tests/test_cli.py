from datetime import datetime, timezone

from crypto_simulator.cli import _load_named_universe
from crypto_simulator.dataset import write_ohlcv_csv
from crypto_simulator.models import OHLCVBar


def test_cli_uses_csv_symbol_as_canonical_key_and_keeps_alias(tmp_path) -> None:
    path = tmp_path / "eth.csv"
    write_ohlcv_csv(
        path,
        [
            OHLCVBar(
                "mexc",
                "ETH_USDT",
                "perpetual",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                "100",
                "101",
                "99",
                "100",
                "10",
            )
        ],
    )

    universe, aliases = _load_named_universe([("ETH", path)])

    assert list(universe) == ["ETH_USDT"]
    assert aliases["ETH"] == "ETH_USDT"
    assert aliases["ETH_USDT"] == "ETH_USDT"
