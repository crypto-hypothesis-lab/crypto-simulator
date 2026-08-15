from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .adapters import BinanceAdapter, BitbankAdapter, CcxtPublicAdapter, GmoCoinAdapter, HyperliquidAdapter
from .backtest import BacktestConfig, run_backtest
from .dataset import load_ohlcv_csv, merge_ohlcv_csv, write_ohlcv_csv
from .models import OHLCVBar
from .research import research_report
from .signals import build_signal_event
from .strategy import MultiTimeframeStrategy, SmaCrossStrategy


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
    return {"binance": BinanceAdapter, "hyperliquid": HyperliquidAdapter, "bitbank": BitbankAdapter, "gmo": GmoCoinAdapter}[name]()


def _write_csv(path: Path, bars: list[OHLCVBar]) -> None:
    write_ohlcv_csv(path, bars)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_window_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--hours", type=int, help="relative lookback in hours")
    command.add_argument("--days", type=int, help="relative lookback in days")
    command.add_argument("--start", help="UTC ISO-8601 start, for example 2025-08-15T00:00:00Z")
    command.add_argument("--end", help="UTC ISO-8601 end, for example 2026-08-15T00:00:00Z")


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("--start/--end must include a timezone, such as Z")
    return parsed.astimezone(timezone.utc)


def _resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    relative = [value for value in (args.hours, args.days) if value is not None]
    if len(relative) > 1:
        raise ValueError("use only one of --hours or --days")
    if relative and args.start:
        raise ValueError("--start cannot be combined with --hours or --days")
    if relative and relative[0] <= 0:
        raise ValueError("relative lookback must be positive")

    end = _parse_utc_datetime(args.end) if args.end else datetime.now(timezone.utc)
    if args.start:
        start = _parse_utc_datetime(args.start)
    else:
        if args.days is not None:
            duration = timedelta(days=args.days)
        else:
            duration = timedelta(hours=args.hours if args.hours is not None else 72)
        start = end - duration
    if start >= end:
        raise ValueError("--start must be earlier than --end")
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(prog="crypto-simulator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("demo")
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--exchange", choices=["binance", "hyperliquid", "bitbank", "gmo", "ccxt"], required=True)
    fetch.add_argument("--ccxt-id")
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--interval", required=True)
    _add_window_arguments(fetch)
    fetch.add_argument("--output", type=Path, required=True)
    collect = subparsers.add_parser("collect", help="fetch and merge a rolling public dataset")
    collect.add_argument("--exchange", choices=["binance", "hyperliquid", "bitbank", "gmo", "ccxt"], default="bitbank")
    collect.add_argument("--ccxt-id")
    collect.add_argument("--symbol", default="btc_jpy")
    collect.add_argument("--interval", default="1hour")
    _add_window_arguments(collect)
    collect.add_argument("--output", type=Path, default=Path("data/bitbank_btc_jpy_1hour.csv"))
    backtest = subparsers.add_parser("backtest", help="run the baseline strategy against a CSV dataset")
    backtest.add_argument("--input", type=Path, required=True)
    backtest.add_argument("--fast", type=int, default=20)
    backtest.add_argument("--slow", type=int, default=50)
    backtest.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    backtest.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    backtest.add_argument("--spread-bps", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--market-impact-bps", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--max-holding-days", type=int, default=30)
    backtest.add_argument("--single-timeframe", action="store_true", help="disable the 4-hour and 1-day filters")
    backtest.add_argument("--trend-fast", type=int, default=5)
    backtest.add_argument("--trend-slow", type=int, default=20)
    backtest.add_argument("--regime-fast", type=int, default=5)
    backtest.add_argument("--regime-slow", type=int, default=20)
    research = subparsers.add_parser("research", help="compare a finite strategy grid with walk-forward validation")
    research.add_argument("--input", type=Path, required=True)
    research.add_argument("--output", type=Path, default=Path("state/strategy-search.json"))
    research.add_argument("--interval", default="1hour")
    research.add_argument("--train-days", type=int, default=180)
    research.add_argument("--test-days", type=int, default=30)
    research.add_argument("--step-days", type=int, default=30)
    research.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    research.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    research.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    research.add_argument("--spread-bps", type=Decimal, default=Decimal("0"))
    research.add_argument("--market-impact-bps", type=Decimal, default=Decimal("0"))
    research.add_argument("--max-holding-days", type=int, default=30)
    signal = subparsers.add_parser("signal", help="write a paper-trading signal from the latest closed candle")
    signal.add_argument("--input", type=Path, required=True)
    signal.add_argument("--output", type=Path, required=True)
    signal.add_argument("--interval", default="1hour")
    signal.add_argument("--fast", type=int, default=20)
    signal.add_argument("--slow", type=int, default=50)
    signal.add_argument("--single-timeframe", action="store_true", help="disable the 4-hour and 1-day filters")
    signal.add_argument("--trend-fast", type=int, default=5)
    signal.add_argument("--trend-slow", type=int, default=20)
    signal.add_argument("--regime-fast", type=int, default=5)
    signal.add_argument("--regime-slow", type=int, default=20)
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
        strategy = (
            SmaCrossStrategy(args.fast, args.slow)
            if args.single_timeframe
            else MultiTimeframeStrategy(
                execution_fast=args.fast,
                execution_slow=args.slow,
                trend_fast=args.trend_fast,
                trend_slow=args.trend_slow,
                regime_fast=args.regime_fast,
                regime_slow=args.regime_slow,
            )
        )
        result = run_backtest(
            bars,
            strategy,
            BacktestConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                spread_bps=args.spread_bps,
                market_impact_bps=args.market_impact_bps,
                max_holding_days=args.max_holding_days,
            ),
        )
        print(f"bars={len(bars)} trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
        return

    if args.command == "research":
        bars = load_ohlcv_csv(args.input)
        report = research_report(
            bars,
            interval=args.interval,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            config=BacktestConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                spread_bps=args.spread_bps,
                market_impact_bps=args.market_impact_bps,
                max_holding_days=args.max_holding_days,
            ),
        )
        _write_json(args.output, report)
        summary = report["summary"]
        print(
            f"saved={args.output} candidates={summary['candidate_count']} "
            f"walk_forward_windows={summary['walk_forward_windows']} status={summary['status']}"
        )
        return

    if args.command == "signal":
        event = build_signal_event(
            load_ohlcv_csv(args.input),
            interval=args.interval,
            fast_window=args.fast,
            slow_window=args.slow,
            trend_fast_window=args.trend_fast,
            trend_slow_window=args.trend_slow,
            regime_fast_window=args.regime_fast,
            regime_slow_window=args.regime_slow,
            multi_timeframe=not args.single_timeframe,
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

    try:
        start, end = _resolve_window(args)
    except ValueError as exc:
        parser.error(str(exc))
    bars = _adapter(args.exchange, args.ccxt_id).fetch_ohlcv(args.symbol, args.interval, start=start, end=end)
    if args.command == "collect":
        added, total = merge_ohlcv_csv(args.output, bars)
        last_timestamp = bars[-1].timestamp.isoformat() if bars else "none"
        print(f"saved={args.output} fetched={len(bars)} added={added} total={total} last_timestamp={last_timestamp}")
    else:
        _write_csv(args.output, bars)
        print(f"saved={args.output} bars={len(bars)}")
