from __future__ import annotations

import csv
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "paper-event"

# These are deliberately small, venue-specific Paper universes. The strategy
# is identical across venues; the exchange, market type, fees, and symbol
# conventions remain visible in the resulting signal lineage.
VENUES = {
    "bitbank": {
        "market": "spot",
        "days": "95",
        "exchange": "bitbank",
        "symbols": [("BTC", "btc_jpy")],
    },
    "hyperliquid": {
        "market": "perpetual",
        "days": "365",
        "exchange": "hyperliquid",
        "symbols": [("BTC", "BTC"), ("ETH", "ETH")],
    },
    "mexc": {
        "market": "perpetual",
        "days": "365",
        "exchange": "mexc",
        "symbols": [("BTC", "BTC_USDT"), ("ETH", "ETH_USDT"), ("SOL", "SOL_USDT"), ("XRP", "XRP_USDT")],
    },
}


def run(*args: str) -> None:
    command = [sys.executable, "-m", "crypto_simulator", *args]
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def compact_bars(paths: list[Path], destination: Path, limit: int = 96) -> None:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                rows.append(
                    {
                        "symbol": row.get("symbol", ""),
                        "timestamp": row.get("timestamp", ""),
                        "open": row.get("open", ""),
                        "high": row.get("high", ""),
                        "low": row.get("low", ""),
                        "close": row.get("close", ""),
                        "volume": row.get("volume", ""),
                    }
                )
    rows = [row for row in rows if row["symbol"] and row["timestamp"]]
    symbols = sorted({row["symbol"] for row in rows})
    selected = []
    for symbol in symbols:
        symbol_rows = sorted((row for row in rows if row["symbol"] == symbol), key=lambda row: row["timestamp"])
        selected.extend(symbol_rows[-limit:])
    selected.sort(key=lambda row: (row["timestamp"], row["symbol"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(selected)


def generate(venue: str, config: dict[str, object], workdir: Path) -> None:
    raw_paths: list[Path] = []
    signal_inputs: list[str] = []
    for alias, symbol in config["symbols"]:
        raw_path = workdir / f"{venue}-{alias}.csv"
        run(
            "fetch",
            "--exchange",
            str(config["exchange"]),
            "--symbol",
            str(symbol),
            "--interval",
            "1h" if venue != "bitbank" else "1hour",
            "--days",
            str(config["days"]),
            "--output",
            str(raw_path),
        )
        raw_paths.append(raw_path)
        signal_inputs.extend(["--input", f"{alias}={raw_path}"])

    signal_path = OUTPUT_ROOT / f"{venue}-event-permission-bracket-signal.json"
    run(
        "limit-bracket-signal",
        "--market",
        str(config["market"]),
        "--profile",
        "event-permission",
        "--strategy-venue",
        venue,
        "--interval",
        "1hour",
        "--benchmark-symbol",
        "BTC",
        "--max-gross-leverage",
        "1",
        "--output",
        str(signal_path),
        *signal_inputs,
    )
    compact_bars(raw_paths, OUTPUT_ROOT / f"{venue}-event-permission-bars.csv")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="crypto-paper-event-") as temporary:
        workdir = Path(temporary)
        for venue, config in VENUES.items():
            generate(venue, config, workdir)


if __name__ == "__main__":
    main()
