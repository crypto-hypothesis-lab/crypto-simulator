from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .models import OHLCVBar


CSV_FIELDS = [
    "exchange",
    "symbol",
    "market_type",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
]


def load_ohlcv_csv(path: str | Path) -> list[OHLCVBar]:
    path = Path(path)
    if not path.exists():
        return []
    bars: dict[tuple[str, str, str, int], OHLCVBar] = {}
    with path.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if not row:
                continue
            bar = OHLCVBar(
                exchange=row["exchange"],
                symbol=row["symbol"],
                market_type=row["market_type"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                quote_volume=row.get("quote_volume") or None,
            )
            bars[(bar.exchange, bar.symbol, bar.market_type, bar.epoch_ms)] = bar
    return [bars[key] for key in sorted(bars, key=lambda key: key[-1])]


def write_ohlcv_csv(path: str | Path, bars: list[OHLCVBar]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = file.name
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for bar in sorted(bars, key=lambda item: (item.timestamp, item.exchange, item.symbol)):
                writer.writerow(bar.to_dict())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)


def merge_ohlcv_csv(path: str | Path, new_bars: list[OHLCVBar]) -> tuple[int, int]:
    """Merge candles by venue/symbol/market/timestamp and return (added, total)."""

    existing = load_ohlcv_csv(path)
    merged = {
        (bar.exchange, bar.symbol, bar.market_type, bar.epoch_ms): bar
        for bar in existing
    }
    before = len(merged)
    for bar in new_bars:
        merged[(bar.exchange, bar.symbol, bar.market_type, bar.epoch_ms)] = bar
    merged_bars = list(merged.values())
    if new_bars or not Path(path).exists():
        write_ohlcv_csv(path, merged_bars)
    return len(merged) - before, len(merged)
