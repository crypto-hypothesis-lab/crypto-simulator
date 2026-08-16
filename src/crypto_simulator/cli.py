from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .adapters import BinanceAdapter, BitbankAdapter, CcxtPublicAdapter, GmoCoinAdapter, HyperliquidAdapter
from .backtest import BacktestConfig, run_backtest
from .dataset import load_funding_json, load_ohlcv_csv, merge_ohlcv_csv, write_funding_json, write_ohlcv_csv
from .models import OHLCVBar
from .portfolio import PortfolioConfig, default_theme_specs, funding_rates_by_interval, portfolio_research_report
from .research import StrategySpec, forward_test_report, research_report
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


def _add_strategy_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--fast", type=int, default=20)
    command.add_argument("--slow", type=int, default=50)
    command.add_argument("--single-timeframe", action="store_true", help="disable the 4-hour and 1-day filters")
    command.add_argument("--trend-fast", type=int, default=5)
    command.add_argument("--trend-slow", type=int, default=20)
    command.add_argument("--regime-fast", type=int, default=5)
    command.add_argument("--regime-slow", type=int, default=20)


def _frozen_strategy_spec(args: argparse.Namespace) -> StrategySpec:
    if args.single_timeframe:
        return StrategySpec(
            f"single_sma_{args.fast}_{args.slow}",
            args.fast,
            args.slow,
            single_timeframe=True,
        )
    return StrategySpec(
        f"mtf_{args.fast}_{args.slow}_{args.trend_fast}_{args.trend_slow}_{args.regime_fast}_{args.regime_slow}",
        args.fast,
        args.slow,
        args.trend_fast,
        args.trend_slow,
        args.regime_fast,
        args.regime_slow,
    )


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


def _parse_portfolio_input(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise ValueError("portfolio --input must use SYMBOL=CSV_PATH")
    symbol, path = value.split("=", 1)
    if not symbol or not path:
        raise ValueError("portfolio --input must use SYMBOL=CSV_PATH")
    return symbol, Path(path)


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
    fetch_funding = subparsers.add_parser("fetch-funding", help="fetch public HyperLiquid perpetual funding history")
    fetch_funding.add_argument("--exchange", choices=["hyperliquid"], default="hyperliquid")
    fetch_funding.add_argument("--symbol", required=True)
    _add_window_arguments(fetch_funding)
    fetch_funding.add_argument("--output", type=Path, required=True)
    collect = subparsers.add_parser("collect", help="fetch and merge a rolling public dataset")
    collect.add_argument("--exchange", choices=["binance", "hyperliquid", "bitbank", "gmo", "ccxt"], default="bitbank")
    collect.add_argument("--ccxt-id")
    collect.add_argument("--symbol", default="btc_jpy")
    collect.add_argument("--interval", default="1hour")
    _add_window_arguments(collect)
    collect.add_argument("--output", type=Path, default=Path("data/bitbank_btc_jpy_1hour.csv"))
    backtest = subparsers.add_parser("backtest", help="run the baseline strategy against a CSV dataset")
    backtest.add_argument("--input", type=Path, required=True)
    _add_strategy_arguments(backtest)
    backtest.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    backtest.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    backtest.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    backtest.add_argument("--spread-bps", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--market-impact-bps", type=Decimal, default=Decimal("0"))
    backtest.add_argument("--max-holding-days", type=int, default=30)
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
    forward = subparsers.add_parser("forward-test", help="evaluate one frozen strategy on the latest holdout window")
    forward.add_argument("--input", type=Path, required=True)
    forward.add_argument("--output", type=Path, default=Path("state/forward-test.json"))
    forward.add_argument("--interval", default="1hour")
    forward.add_argument("--holdout-days", type=int, default=30)
    _add_strategy_arguments(forward)
    forward.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    forward.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    forward.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    forward.add_argument("--spread-bps", type=Decimal, default=Decimal("0"))
    forward.add_argument("--market-impact-bps", type=Decimal, default=Decimal("0"))
    forward.add_argument("--max-holding-days", type=int, default=30)
    portfolio = subparsers.add_parser("portfolio-research", help="research regime-aware cross-sectional spot/perpetual strategies")
    portfolio.add_argument("--market", choices=["spot", "perpetual"], required=True)
    portfolio.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    portfolio.add_argument("--funding", dest="fundings", action="append", type=Path, help="funding JSON from fetch-funding; repeat per symbol")
    portfolio.add_argument("--benchmark-symbol")
    portfolio.add_argument("--output", type=Path, default=Path("state/portfolio-strategy-search.json"))
    portfolio.add_argument("--interval", default="1day")
    portfolio.add_argument("--train-days", type=int, default=180)
    portfolio.add_argument("--test-days", type=int, default=60)
    portfolio.add_argument("--step-days", type=int, default=60)
    portfolio.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    portfolio.add_argument("--fee-bps", type=Decimal, default=Decimal("10"))
    portfolio.add_argument("--slippage-bps", type=Decimal, default=Decimal("5"))
    portfolio.add_argument("--spread-bps", type=Decimal, default=Decimal("0"))
    portfolio.add_argument("--market-impact-bps", type=Decimal, default=Decimal("0"))
    portfolio.add_argument("--rebalance-every-bars", type=int, default=1)
    portfolio.add_argument("--max-gross-leverage", type=Decimal)
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

    if args.command == "forward-test":
        bars = load_ohlcv_csv(args.input)
        report = forward_test_report(
            bars,
            _frozen_strategy_spec(args),
            BacktestConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                spread_bps=args.spread_bps,
                market_impact_bps=args.market_impact_bps,
                max_holding_days=args.max_holding_days,
            ),
            holdout_days=args.holdout_days,
            interval=args.interval,
        )
        _write_json(args.output, report)
        print(
            f"saved={args.output} strategy={report['strategy']['name']} "
            f"holdout_bars={report['holdout']['bars']} status={report['status']}"
        )
        return

    if args.command == "portfolio-research":
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
        except ValueError as exc:
            parser.error(str(exc))
        universe = {symbol: load_ohlcv_csv(path) for symbol, path in parsed_inputs}
        specs = default_theme_specs(args.market)
        if args.benchmark_symbol:
            specs = [replace(spec, benchmark_symbol=args.benchmark_symbol) for spec in specs]
        funding_rates = None
        if args.fundings:
            points = [point for path in args.fundings for point in load_funding_json(path)]
            funding_rates = funding_rates_by_interval(points, args.interval)
            aliases = {symbol.upper(): symbol for symbol in universe}
            funding_rates = {
                aliases.get(symbol.upper(), symbol): rates
                for symbol, rates in funding_rates.items()
            }
        leverage = args.max_gross_leverage
        if leverage is None:
            leverage = Decimal("1") if args.market == "spot" else Decimal("1.5")
        report = portfolio_research_report(
            universe,
            market=args.market,
            specs=specs,
            funding_rates=funding_rates,
            interval=args.interval,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            config=PortfolioConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps,
                slippage_bps=args.slippage_bps,
                spread_bps=args.spread_bps,
                market_impact_bps=args.market_impact_bps,
                rebalance_every_bars=args.rebalance_every_bars,
                max_gross_leverage=leverage,
            ),
        )
        _write_json(args.output, report)
        summary = report["summary"]
        print(
            f"saved={args.output} market={args.market} candidates={summary['candidate_count']} "
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

    if args.command == "fetch-funding":
        try:
            start, end = _resolve_window(args)
        except ValueError as exc:
            parser.error(str(exc))
        points = HyperliquidAdapter().fetch_funding(args.symbol, start=start, end=end)
        write_funding_json(args.output, points)
        print(f"saved={args.output} points={len(points)}")
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
