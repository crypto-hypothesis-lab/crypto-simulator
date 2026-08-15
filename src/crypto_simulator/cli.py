from __future__ import annotations

import argparse
import csv
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .adapters import BitbankAdapter, GmoCoinAdapter, HyperliquidAdapter
from .backtest import BacktestConfig, run_backtest
from .models import OHLCVBar
from .strategy import SmaCrossStrategy


def synthetic_bars(count: int = 120) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = Decimal("100") + Decimal(str(12 * math.sin(index / 11))) + Decimal(index) / Decimal("8")
        open_ = close - Decimal("0.4")
        bars.append(OHLCVBar("synthetic", "TEST/USDT", "spot", start + timedelta(hours=index), open_, close + Decimal("1"), close - Decimal("1"), close, Decimal("1000")))
    return bars


def _adapter(name: str):
    return {"hyperliquid": HyperliquidAdapter, "bitbank": BitbankAdapter, "gmo": GmoCoinAdapter}[name]()


def _write_csv(path: Path, bars: list[OHLCVBar]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=["exchange", "symbol", "market_type", "timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for bar in bars:
            writer.writerow(bar.to_dict())


def main() -> None:
    parser = argparse.ArgumentParser(prog="crypto-simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo")
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--exchange", choices=["hyperliquid", "bitbank", "gmo"], required=True)
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--interval", required=True)
    fetch.add_argument("--hours", type=int, default=72)
    fetch.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "demo":
        result = run_backtest(synthetic_bars(), SmaCrossStrategy(5, 20), BacktestConfig(initial_cash=Decimal("100000")))
        print(f"trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
        return

    end = datetime.now(timezone.utc)
    bars = _adapter(args.exchange).fetch_ohlcv(args.symbol, args.interval, start=end - timedelta(hours=args.hours), end=end)
    _write_csv(args.output, bars)
    print(f"saved={args.output} bars={len(bars)}")
