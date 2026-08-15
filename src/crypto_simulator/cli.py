from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .adapters import BitbankAdapter, CcxtPublicAdapter, GmoCoinAdapter, HyperliquidAdapter
from .backtest import BacktestConfig, run_backtest
from .dataset import load_ohlcv_csv, merge_ohlcv_csv, write_ohlcv_csv
from .models import OHLCVBar
from .signals import build_signal_event
from .strategy import SmaCrossStrategy


def synthetic_bars(count: int = 120) -> list[OHLCVBar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index in range(count):
        close = Decimal("100") + Decimal(str(12 * math.sin(index / 11))) + Decimal(index) / Decimal("8")
        open_ = close - Decimal("0.4")
        bars.append(OHLCVBar("synthetic", "TEST/USDT", "spot", start + timedelta(hours=index), open_, close + Decimal("1"), close - Decimal("1"), close, Decimal("1000")))
    return bars


def _adapter(name: str, ccxt_id: str | None = None):
    if name == "ccxt":
        if not ccxt_id:
            raise ValueError("--ccxt-id is required when --exchange ccxt is used")
        return CcxtPublicAdapter(ccxt_id)
    return {"hyperliquid": HyperliquidAdapter, "bitbank": BitbankAdapter, "gmo": GmoCoinAdapter}[name]()


def _write_csv(path: Path, bars: list[OHLCVBar]) -> None:
    write_ohlcv_csv(path, bars)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(prog="crypto-simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo")
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--exchange", choices=["hyperliquid", "bitbank", "gmo", "ccxt"], required=True)
    fetch.add_argument("--ccxt-id")
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--interval", required=True)
    fetch.add_argument("--hours", type=int, default=72)
    fetch.add_argument("--output", type=Path, required=True)
    collect = subparsers.add_parser("collect", help="fetch and merge a rolling public dataset")
    collect.add_argument("--exchange", choices=["hyperliquid", "bitbank", "gmo", "ccxt"], default="bitbank")
    collect.add_argument("--ccxt-id")
    collect.add_argument("--symbol", default="btc_jpy")
    collect.add_argument("--interval", default="1hour")
    collect.add_argument("--hours", type=int, default=72)
    collect.add_argument("--output", type=Path, default=Path("data/bitbank_btc_jpy_1hour.csv"))
    backtest = subparsers.add_parser("backtest", help="run the baseline strategy against a CSV dataset")
    backtest.add_argument("--input", type=Path, required=True)
    backtest.add_argument("--fast", type=int, default=20)
    backtest.add_argument("--slow", type=int, default=50)
    backtest.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    backtest.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    signal = subparsers.add_parser("signal", help="write a paper-trading signal from the latest closed candle")
    signal.add_argument("--input", type=Path, required=True)
    signal.add_argument("--output", type=Path, required=True)
    signal.add_argument("--interval", default="1hour")
    signal.add_argument("--fast", type=int, default=20)
    signal.add_argument("--slow", type=int, default=50)
    duckdb_import = subparsers.add_parser("duckdb-import", help="import normalized CSV candles into local DuckDB")
    duckdb_import.add_argument("--input", type=Path, required=True)
    duckdb_import.add_argument("--database", type=Path, default=Path("data/crypto-market.duckdb"))
    args = parser.parse_args()

    if args.command == "demo":
        result = run_backtest(synthetic_bars(), SmaCrossStrategy(5, 20), BacktestConfig(initial_cash=Decimal("100000")))
        print(f"trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
        return

    if args.command == "backtest":
        bars = load_ohlcv_csv(args.input)
        result = run_backtest(
            bars,
            SmaCrossStrategy(args.fast, args.slow),
            BacktestConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
            ),
        )
        print(f"bars={len(bars)} trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
        return

    if args.command == "signal":
        event = build_signal_event(
            load_ohlcv_csv(args.input),
            interval=args.interval,
            fast_window=args.fast,
            slow_window=args.slow,
        )
        _write_json(args.output, event)
        print(f"saved={args.output} action={event['action']} timestamp={event['timestamp']}")
        return

    if args.command == "duckdb-import":
        from .duckdb_store import DuckDbCandleStore

        bars = load_ohlcv_csv(args.input)
        imported = DuckDbCandleStore(args.database).upsert(bars)
        print(f"database={args.database} imported={imported}")
        return

    end = datetime.now(timezone.utc)
    bars = _adapter(args.exchange, args.ccxt_id).fetch_ohlcv(args.symbol, args.interval, start=end - timedelta(hours=args.hours), end=end)
    if args.command == "collect":
        added, total = merge_ohlcv_csv(args.output, bars)
        last_timestamp = bars[-1].timestamp.isoformat() if bars else "none"
        print(f"saved={args.output} fetched={len(bars)} added={added} total={total} last_timestamp={last_timestamp}")
    else:
        _write_csv(args.output, bars)
        print(f"saved={args.output} bars={len(bars)}")
