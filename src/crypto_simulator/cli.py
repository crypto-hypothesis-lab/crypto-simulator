from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .adapters import (
    BinanceAdapter,
    BitbankAdapter,
    BybitDerivativesAdapter,
    CcxtPublicAdapter,
    GmoCoinAdapter,
    HyperliquidAdapter,
    HyperliquidDerivativesAdapter,
    MexcContractAdapter,
    MexcDerivativesAdapter,
    OKXDerivativesAdapter,
)
from .backtest import BacktestConfig, run_backtest
from .dataset import load_funding_json, load_ohlcv_csv, merge_ohlcv_csv, write_funding_json, write_ohlcv_csv
from .models import OHLCVBar
from .derivatives import DerivativesFeaturePolicy, DerivativesObservation, build_derivatives_shadow_report
from .portfolio import PortfolioConfig, default_theme_specs, funding_rates_by_interval, portfolio_research_report
from .promotion import PromotionPolicy, evaluate_promotion_gate
from .evaluation import compare_evaluations
from .research_report import build_research_report
from .mexc_liquidity import LiquidityPolicy, assess_liquidity, build_liquidity_manifest, select_current_liquid_tickers
from .market_structure import build_market_structure_event_study
from .research import StrategySpec, evaluate_result, forward_test_report, research_report
from .spike_fade import default_spike_fade_specs, spike_fade_research_report
from .limit_bracket import (
    build_limit_bracket_signal_event,
    default_limit_bracket_specs,
    default_mexc_event_specs,
    default_mexc_event_v2_specs,
    limit_bracket_research_report,
)
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
    return {"binance": BinanceAdapter, "hyperliquid": HyperliquidAdapter, "bitbank": BitbankAdapter, "gmo": GmoCoinAdapter, "mexc": MexcContractAdapter}[name]()


def _write_csv(path: Path, bars: list[OHLCVBar]) -> None:
    write_ohlcv_csv(path, bars)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _record_research(
    database: Path | None,
    payload: dict[str, object],
    *,
    output: Path,
    experiment_id: str | None = None,
    stage: str = "backtest",
    exchange: str | None = None,
) -> str | None:
    if database is None:
        return None
    from .research_ledger import DuckDbResearchLedger

    summary = DuckDbResearchLedger(database).record(
        payload,
        experiment_id=experiment_id,
        stage=stage,
        exchange=exchange,
        source_path=str(output),
    )
    return summary.run_id


def _load_derivatives_observations(path: Path) -> list[DerivativesObservation]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("observations"), list):
        payload = payload["observations"]
    if not isinstance(payload, list):
        raise ValueError("derivatives input must be an array or an object with an observations array")
    return [DerivativesObservation.from_dict(item) for item in payload if isinstance(item, dict)]


def _fetch_derivatives_observations(venues: list[str], symbols: list[str], observed_at: datetime) -> list[DerivativesObservation]:
    result: list[DerivativesObservation] = []
    for venue in venues:
        if venue == "hyperliquid":
            adapter = HyperliquidDerivativesAdapter()
            try:
                result.extend(adapter.fetch_snapshots(symbols, observed_at=observed_at))
            except Exception as exc:
                result.extend(DerivativesObservation.error_observation(venue, symbol, observed_at=observed_at, error=str(exc)) for symbol in symbols)
        else:
            adapter = {"bybit": BybitDerivativesAdapter, "mexc": MexcDerivativesAdapter, "okx": OKXDerivativesAdapter}[venue]()
            for symbol in symbols:
                try:
                    result.append(adapter.fetch_snapshot(symbol, observed_at=observed_at))
                except Exception as exc:
                    result.append(DerivativesObservation.error_observation(venue, symbol, observed_at=observed_at, error=str(exc)))
    return result


def _load_named_universe(parsed_inputs: list[tuple[str, Path]]) -> tuple[dict[str, list[OHLCVBar]], dict[str, str]]:
    """Load each CSV under its canonical in-file symbol and retain CLI aliases."""

    universe: dict[str, list[OHLCVBar]] = {}
    aliases: dict[str, str] = {}
    for requested_symbol, path in parsed_inputs:
        bars = load_ohlcv_csv(path)
        symbols = {bar.symbol for bar in bars}
        if len(symbols) != 1:
            raise ValueError(f"{path} must contain exactly one symbol")
        canonical_symbol = next(iter(symbols))
        if canonical_symbol in universe:
            raise ValueError(f"duplicate canonical symbol in inputs: {canonical_symbol}")
        universe[canonical_symbol] = bars
        aliases[requested_symbol.upper()] = canonical_symbol
        aliases[canonical_symbol.upper()] = canonical_symbol
    return universe, aliases


def _add_strategy_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--fast", type=int, default=20)
    command.add_argument("--slow", type=int, default=50)
    command.add_argument("--single-timeframe", action="store_true", help="disable the 4-hour and 1-day filters")
    command.add_argument("--trend-fast", type=int, default=5)
    command.add_argument("--trend-slow", type=int, default=20)
    command.add_argument("--regime-fast", type=int, default=5)
    command.add_argument("--regime-slow", type=int, default=20)


def _add_research_ledger_argument(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--research-database",
        type=Path,
        help="append the complete report and normalized metrics to a local DuckDB research ledger",
    )


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
    fetch.add_argument("--exchange", choices=["binance", "hyperliquid", "bitbank", "gmo", "mexc", "ccxt"], required=True)
    fetch.add_argument("--ccxt-id")
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--interval", required=True)
    _add_window_arguments(fetch)
    fetch.add_argument("--output", type=Path, required=True)
    fetch_funding = subparsers.add_parser("fetch-funding", help="fetch public HyperLiquid perpetual funding history")
    fetch_funding.add_argument("--exchange", choices=["hyperliquid", "mexc"], default="hyperliquid")
    fetch_funding.add_argument("--symbol", required=True)
    _add_window_arguments(fetch_funding)
    fetch_funding.add_argument("--output", type=Path, required=True)
    derivatives_shadow = subparsers.add_parser("derivatives-shadow", help="collect public derivatives data and write a Shadow-only regime report")
    derivatives_shadow.add_argument("--venue", action="append", choices=["hyperliquid", "mexc", "bybit", "okx"], help="repeat to select public venues; default: Hyperliquid, MEXC, Bybit, and OKX")
    derivatives_shadow.add_argument("--symbol", action="append", help="repeat for BTC/ETH-style perpetual symbols; default: BTC and ETH")
    derivatives_shadow.add_argument("--input", type=Path, help="offline observation JSON fixture; skips live public API collection")
    derivatives_shadow.add_argument("--history", type=Path, help="additional historical observation JSON used for point-in-time changes")
    derivatives_shadow.add_argument("--as-of", help="UTC ISO-8601 evaluation timestamp; defaults to now")
    derivatives_shadow.add_argument("--min-venues", type=int, default=2)
    derivatives_shadow.add_argument("--database", type=Path, help="optional local DuckDB path for append-only observation history")
    derivatives_shadow.add_argument("--output", type=Path, default=Path("state/derivatives-shadow.json"))
    mexc_liquid = subparsers.add_parser("mexc-liquid-select", help="select current MEXC perpetuals by public liquidity")
    mexc_liquid.add_argument("--output", type=Path, required=True)
    mexc_liquid.add_argument("--max-symbols", type=int, default=20)
    mexc_liquid.add_argument("--min-quote-turnover-24h", type=Decimal, default=Decimal("10000000"))
    mexc_liquid.add_argument("--max-spread-bps", type=Decimal, default=Decimal("25"))
    mexc_liquid.add_argument("--benchmark-symbol", default="BTC_USDT")
    mexc_liquid.add_argument("--exclude-symbol", action="append", default=[])
    mexc_audit = subparsers.add_parser("mexc-liquidity-audit", help="audit historical MEXC quote turnover and volume regime")
    mexc_audit.add_argument("--selection", type=Path, required=True)
    mexc_audit.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    mexc_audit.add_argument("--output", type=Path, required=True)
    mexc_audit.add_argument("--interval", default="1hour")
    mexc_audit.add_argument("--max-symbols", type=int, default=20)
    mexc_audit.add_argument("--min-quote-turnover-24h", type=Decimal, default=Decimal("10000000"))
    mexc_audit.add_argument("--min-median-daily-quote-turnover", type=Decimal, default=Decimal("5000000"))
    mexc_audit.add_argument("--max-spread-bps", type=Decimal, default=Decimal("25"))
    mexc_audit.add_argument("--min-history-bars", type=int, default=6000)
    mexc_audit.add_argument("--min-coverage", type=float, default=0.98)
    collect = subparsers.add_parser("collect", help="fetch and merge a rolling public dataset")
    collect.add_argument("--exchange", choices=["binance", "hyperliquid", "bitbank", "gmo", "mexc", "ccxt"], default="bitbank")
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
    backtest.add_argument("--output", type=Path, default=Path("state/backtest.json"))
    _add_research_ledger_argument(backtest)
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
    _add_research_ledger_argument(research)
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
    _add_research_ledger_argument(forward)
    portfolio = subparsers.add_parser("portfolio-research", help="research regime-aware cross-sectional spot/margin/perpetual strategies")
    portfolio.add_argument("--market", choices=["spot", "margin", "perpetual"], required=True)
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
    portfolio.add_argument("--margin-interest-bps-per-day", type=Decimal)
    portfolio.add_argument("--rebalance-every-bars", type=int, default=1)
    portfolio.add_argument("--max-gross-leverage", type=Decimal)
    portfolio.add_argument("--max-leverage-map", type=Path, help="JSON object mapping symbols to exchange leverage caps")
    _add_research_ledger_argument(portfolio)
    spike_fade = subparsers.add_parser("spike-fade-research", help="research short-after-pump exhaustion strategies")
    spike_fade.add_argument("--market", choices=["margin", "perpetual"], required=True)
    spike_fade.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    spike_fade.add_argument("--funding", dest="fundings", action="append", type=Path, help="funding JSON from fetch-funding; repeat per symbol")
    spike_fade.add_argument("--benchmark-symbol")
    spike_fade.add_argument("--output", type=Path, default=Path("state/spike-fade-research.json"))
    spike_fade.add_argument("--interval", default="4hour")
    spike_fade.add_argument("--train-days", type=int, default=240)
    spike_fade.add_argument("--test-days", type=int, default=60)
    spike_fade.add_argument("--step-days", type=int, default=60)
    spike_fade.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    spike_fade.add_argument("--fee-bps", type=Decimal)
    spike_fade.add_argument("--slippage-bps", type=Decimal)
    spike_fade.add_argument("--spread-bps", type=Decimal)
    spike_fade.add_argument("--market-impact-bps", type=Decimal)
    spike_fade.add_argument("--margin-interest-bps-per-day", type=Decimal)
    spike_fade.add_argument("--max-gross-leverage", type=Decimal)
    spike_fade.add_argument("--max-leverage-map", type=Path, help="JSON object mapping symbols to exchange leverage caps")
    _add_research_ledger_argument(spike_fade)
    limit_bracket = subparsers.add_parser("limit-bracket-research", help="research multi-timeframe limit-entry bracket strategies")
    limit_bracket.add_argument("--market", choices=["spot", "margin", "perpetual"], required=True)
    limit_bracket.add_argument("--profile", choices=["standard", "mexc-event", "mexc-event-v2"], default="standard")
    limit_bracket.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    limit_bracket.add_argument("--funding", dest="fundings", action="append", type=Path, help="funding JSON from fetch-funding; repeat per symbol")
    limit_bracket.add_argument("--benchmark-symbol")
    limit_bracket.add_argument("--output", type=Path, default=Path("state/limit-bracket-research.json"))
    limit_bracket.add_argument("--interval", default="1hour")
    limit_bracket.add_argument("--train-days", type=int, default=180)
    limit_bracket.add_argument("--test-days", type=int, default=60)
    limit_bracket.add_argument("--step-days", type=int, default=60)
    limit_bracket.add_argument("--minimum-training-trades", type=int, default=20)
    limit_bracket.add_argument("--initial-cash", type=Decimal, default=Decimal("100000"))
    limit_bracket.add_argument("--fee-bps", type=Decimal)
    limit_bracket.add_argument("--maker-fee-bps", type=Decimal)
    limit_bracket.add_argument("--taker-fee-bps", type=Decimal)
    limit_bracket.add_argument("--slippage-bps", type=Decimal)
    limit_bracket.add_argument("--spread-bps", type=Decimal)
    limit_bracket.add_argument("--market-impact-bps", type=Decimal)
    limit_bracket.add_argument("--adverse-selection-bps", type=Decimal, default=Decimal("0"))
    limit_bracket.add_argument("--stop-gap-penalty-bps", type=Decimal, default=Decimal("0"))
    limit_bracket.add_argument("--margin-interest-bps-per-day", type=Decimal)
    limit_bracket.add_argument("--max-gross-leverage", type=Decimal)
    limit_bracket.add_argument("--max-leverage-map", type=Path, help="JSON object mapping symbols to exchange leverage caps")
    _add_research_ledger_argument(limit_bracket)
    bracket_signal = subparsers.add_parser("limit-bracket-signal", help="write the latest investment decision snapshot")
    bracket_signal.add_argument("--market", choices=["spot", "margin", "perpetual"], required=True)
    bracket_signal.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    bracket_signal.add_argument("--benchmark-symbol")
    bracket_signal.add_argument("--funding", dest="fundings", action="append", type=Path, help="funding JSON from fetch-funding; repeat per symbol")
    bracket_signal.add_argument("--output", type=Path, default=Path("state/latest-bracket-signal.json"))
    bracket_signal.add_argument("--interval", default="1hour")
    bracket_signal.add_argument(
        "--profile",
        choices=[
            "fast",
            "balanced",
            "deep",
            "mexc-short",
            "mexc-long",
            "mexc-short-rejection",
            "mexc-short-v2",
            "mexc-long-v2",
            "mexc-short-rejection-v2",
        ],
        default="balanced",
    )
    bracket_signal.add_argument("--max-gross-leverage", type=Decimal)
    bracket_signal.add_argument("--max-leverage-map", type=Path, help="JSON object mapping symbols to exchange leverage caps")
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
    market_structure = subparsers.add_parser("market-structure-study", help="run point-in-time OI/Funding event studies without creating orders")
    market_structure.add_argument("--input", dest="inputs", action="append", required=True, metavar="SYMBOL=CSV_PATH")
    market_structure.add_argument("--derivatives", type=Path, help="derivatives observation JSON or shadow report")
    market_structure.add_argument("--derivatives-database", type=Path, help="DuckDB containing derivatives_observations")
    market_structure.add_argument("--min-venues", type=int, default=1)
    market_structure.add_argument("--output", type=Path, default=Path("state/market-structure-study.json"))
    market_structure.add_argument("--research-database", type=Path, default=Path("data/research-ledger.duckdb"))
    research_record = subparsers.add_parser("research-record", help="archive any existing JSON research result, including failures")
    research_record.add_argument("--input", type=Path, required=True)
    research_record.add_argument("--database", type=Path, default=Path("data/research-ledger.duckdb"))
    research_record.add_argument("--experiment-id")
    research_record.add_argument("--stage", choices=["backtest", "walk_forward", "cost_stress", "forward_test", "paper", "shadow_live", "small_live", "production"], default="backtest")
    research_record.add_argument("--exchange")
    research_record.add_argument("--hypothesis")
    research_record.add_argument("--conclusion")
    research_record.add_argument("--tag", action="append", default=[])
    research_history = subparsers.add_parser("research-history", help="export compact research-ledger history for the next experiment")
    research_history.add_argument("--database", type=Path, default=Path("data/research-ledger.duckdb"))
    research_history.add_argument("--output", type=Path, default=Path("state/research-history.json"))
    research_history.add_argument("--failure-output", type=Path, help="optional Git-friendly failure-reasons JSON")
    research_history.add_argument("--limit", type=int, default=100)
    promotion = subparsers.add_parser("promotion-gate", help="evaluate a causal cost-aware Paper promotion gate")
    promotion.add_argument("--input", type=Path, required=True, help="JSON array or object with an outcomes array")
    promotion.add_argument("--output", type=Path, default=Path("state/promotion-gate.json"))
    promotion.add_argument("--cost-reserve", type=float, default=0.0051)
    promotion.add_argument("--minimum-net-ev", type=float, default=0.002)
    promotion.add_argument("--minimum-distinct-days", type=int, default=30)
    promotion.add_argument("--minimum-profit-factor", type=float)
    promotion.add_argument("--minimum-expectancy", type=float)
    promotion.add_argument("--maximum-drawdown", type=float)
    promotion.add_argument("--stage", choices=["backtest", "walk_forward", "cost_stress", "forward_test", "paper", "shadow_live", "small_live", "production"], default="paper")
    compare = subparsers.add_parser("paper-compare", help="compare Backtest, Forward Test and Paper outcomes")
    compare.add_argument("--backtest", type=Path, required=True)
    compare.add_argument("--forward-test", type=Path, required=True)
    compare.add_argument("--paper", type=Path, required=True)
    compare.add_argument("--output", type=Path, default=Path("state/paper-compare.json"))
    compare.add_argument("--cost-reserve", type=float, default=0.0)
    compare.add_argument("--minimum-paper-outcomes", type=int, default=5)
    research_report_command = subparsers.add_parser("research-report", help="build a Discord/Paper decision from a research artifact")
    research_report_command.add_argument("--input", type=Path, required=True)
    research_report_command.add_argument("--output", type=Path, default=Path("state/research-report.json"))
    research_report_command.add_argument("--exchange", choices=["bitbank", "hyperliquid", "mexc"], required=True)
    research_report_command.add_argument("--report-url", default="")
    args = parser.parse_args()

    if args.command == "research-record":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("research artifact must be a JSON object")
            from .research_ledger import DuckDbResearchLedger

            summary = DuckDbResearchLedger(args.database).record(
                payload,
                experiment_id=args.experiment_id,
                stage=args.stage,
                exchange=args.exchange,
                hypothesis=args.hypothesis,
                conclusion=args.conclusion,
                tags=tuple(args.tag),
                source_path=str(args.input),
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"invalid research artifact: {exc}")
        print(f"database={args.database} run_id={summary.run_id} status={summary.status} strategies={len(summary.strategy_ids)}")
        return

    if args.command == "research-history":
        from .research_ledger import DuckDbResearchLedger, FAILURE_SCHEMA_VERSION, LEDGER_SCHEMA_VERSION

        report = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": DuckDbResearchLedger(args.database).evidence(limit=args.limit),
        }
        _write_json(args.output, report)
        if args.failure_output:
            _write_json(
                args.failure_output,
                {
                    "schema_version": FAILURE_SCHEMA_VERSION,
                    "generated_at": report["generated_at"],
                    "runs": [
                        {
                            "run_id": run["run_id"],
                            "experiment_id": run["experiment_id"],
                            "recorded_at": run["recorded_at"],
                            "exchange": run["exchange"],
                            "stage": run["stage"],
                            "status": run["status"],
                            "strategy_ids": run["strategy_ids"],
                            "failure_reasons": run.get("performance_failures", []),
                        }
                        for run in report["runs"]
                        if run.get("performance_failures")
                    ],
                },
            )
        print(f"saved={args.output} runs={len(report['runs'])}")
        return

    if args.command == "market-structure-study":
        if bool(args.derivatives) == bool(args.derivatives_database):
            parser.error("provide exactly one of --derivatives or --derivatives-database")
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
            universe, _ = _load_named_universe(parsed_inputs)
            if args.derivatives:
                observations = _load_derivatives_observations(args.derivatives)
            else:
                from .duckdb_store import DuckDbDerivativesStore

                observations = DuckDbDerivativesStore(args.derivatives_database).load(market_type="perpetual")
            report = build_market_structure_event_study(universe, observations, min_venues=args.min_venues)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            parser.error(f"invalid market-structure input: {exc}")
        _write_json(args.output, report)
        run_id = _record_research(
            args.research_database,
            report,
            output=args.output,
            experiment_id="market-structure-events-v1",
            stage="backtest",
            exchange="mexc",
        )
        print(f"saved={args.output} events={report['summary']['event_count']} ledger_run={run_id}")
        return

    if args.command == "demo":
        result = run_backtest(synthetic_bars(), SmaCrossStrategy(5, 20), BacktestConfig(initial_cash=Decimal("100000")))
        print(f"trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
        return

    if args.command == "promotion-gate":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"invalid promotion input: {exc}")
        outcomes = payload.get("outcomes") if isinstance(payload, dict) else payload
        if not isinstance(outcomes, list) or not all(isinstance(item, dict) for item in outcomes):
            parser.error("promotion input must be a JSON array or an object with an outcomes array")
        report = evaluate_promotion_gate(
            outcomes,
            PromotionPolicy(
                cost_reserve=args.cost_reserve,
                minimum_net_effective_ev=args.minimum_net_ev,
                minimum_distinct_days=args.minimum_distinct_days,
                minimum_profit_factor=args.minimum_profit_factor,
                minimum_expectancy=args.minimum_expectancy,
                maximum_drawdown=args.maximum_drawdown,
            ),
            stage=args.stage,
        )
        _write_json(args.output, report)
        print(f"saved={args.output} decision={report['decision']} outcomes={report['outcome_count']}")
        return

    if args.command == "paper-compare":
        try:
            payloads = [json.loads(path.read_text(encoding="utf-8")) for path in (args.backtest, args.forward_test, args.paper)]
            if not all(isinstance(payload, dict) for payload in payloads):
                raise ValueError("evaluation inputs must be JSON objects")
            report = compare_evaluations(
                payloads[0],
                payloads[1],
                payloads[2],
                cost_reserve=args.cost_reserve,
                minimum_paper_outcomes=args.minimum_paper_outcomes,
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"invalid evaluation input: {exc}")
        _write_json(args.output, report)
        print(f"saved={args.output} status={report['status']}")
        return

    if args.command == "research-report":
        try:
            payload = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("research input must be a JSON object")
            report = build_research_report(payload, exchange=args.exchange, report_url=args.report_url)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"invalid research input: {exc}")
        _write_json(args.output, report)
        print(f"saved={args.output} decision={report['decision']} strategy={report['strategy']['name']}")
        return

    if args.command == "mexc-liquid-select":
        policy = LiquidityPolicy(
            max_symbols=args.max_symbols,
            min_quote_turnover_24h=args.min_quote_turnover_24h,
            max_spread_bps=args.max_spread_bps,
        )
        tickers = MexcContractAdapter().fetch_tickers()
        details = {item.symbol: item for item in MexcContractAdapter().fetch_contract_details()}
        selected = select_current_liquid_tickers(
            tickers,
            policy=policy,
            benchmark_symbol=args.benchmark_symbol,
            excluded_symbols=args.exclude_symbol,
            contract_details=details,
        )
        report = build_liquidity_manifest(selected, policy=policy, benchmark_symbol=args.benchmark_symbol)
        report["ticker_count"] = len(tickers)
        _write_json(args.output, report)
        print(f"saved={args.output} selected={len(selected)} tickers={len(tickers)}")
        return

    if args.command == "mexc-liquidity-audit":
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
            selection = json.loads(args.selection.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            parser.error(f"invalid MEXC liquidity input: {exc}")
        if not isinstance(selection, dict) or not isinstance(selection.get("symbols"), list):
            parser.error("MEXC selection must contain a symbols array")
        universe, _ = _load_named_universe(parsed_inputs)
        selected_rows = {str(row.get("symbol")): row for row in selection["symbols"] if isinstance(row, dict) and row.get("symbol")}
        assessments = {}
        for symbol, bars in universe.items():
            row = selected_rows.get(symbol)
            if row is None:
                parser.error(f"history input is not present in selection: {symbol}")
            spread = Decimal(str(row.get("spread_bps", "999999")))
            assessments[symbol] = assess_liquidity(
                symbol,
                bars,
                interval=args.interval,
                policy=LiquidityPolicy(
                    max_symbols=args.max_symbols,
                    min_quote_turnover_24h=args.min_quote_turnover_24h,
                    min_median_daily_quote_turnover=args.min_median_daily_quote_turnover,
                    max_spread_bps=args.max_spread_bps,
                    min_history_bars=args.min_history_bars,
                    min_coverage=args.min_coverage,
                ),
                spread_bps=spread,
            )
        for row in selection["symbols"]:
            if isinstance(row, dict) and row.get("symbol") in assessments:
                row["history"] = assessments[row["symbol"]].to_dict()
        benchmark = str(selection.get("benchmark_symbol", "BTC_USDT"))
        eligible = [row["symbol"] for row in selection["symbols"] if isinstance(row, dict) and row.get("history", {}).get("passed")]
        if benchmark not in eligible:
            parser.error("benchmark did not pass historical liquidity audit")
        selection["eligible_symbols"] = eligible[: args.max_symbols]
        selection["rejected_symbols"] = [row["symbol"] for row in selection["symbols"] if row["symbol"] not in selection["eligible_symbols"]]
        selection["historical_audit"] = True
        _write_json(args.output, selection)
        print(f"saved={args.output} eligible={len(selection['eligible_symbols'])} rejected={len(selection['rejected_symbols'])}")
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
        config = BacktestConfig(
            initial_cash=args.initial_cash,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
            spread_bps=args.spread_bps,
            market_impact_bps=args.market_impact_bps,
            max_holding_days=args.max_holding_days,
        )
        result = run_backtest(
            bars,
            strategy,
            config,
        )
        strategy_id = (
            f"sma_{args.fast}_{args.slow}_single_v1"
            if args.single_timeframe
            else f"mtf_sma_{args.fast}_{args.slow}_{args.trend_fast}_{args.trend_slow}_{args.regime_fast}_{args.regime_slow}_v1"
        )
        metrics = evaluate_result(result, bars, config)
        report = {
            "schema_version": "crypto.backtest.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "exchange": bars[0].exchange,
                "market": bars[0].market_type,
                "symbols": [bars[0].symbol],
                "start": bars[0].timestamp.isoformat(),
                "end": bars[-1].timestamp.isoformat(),
                "bars": len(bars),
            },
            "method": {
                "market": bars[0].market_type,
                "execution": "closed-bar signal, next-bar open",
                "costs": {
                    "fee_bps": str(config.fee_bps),
                    "slippage_bps": str(config.slippage_bps),
                    "spread_bps": str(config.spread_bps),
                    "market_impact_bps": str(config.market_impact_bps),
                },
            },
            "full_sample": [
                {
                    "strategy": {"name": strategy_id, "strategy_id": strategy_id, "strategy_version": strategy_id},
                    "metrics": asdict(metrics),
                    "trades": [
                        {
                            "timestamp": trade.timestamp,
                            "side": trade.side,
                            "price": str(trade.price),
                            "quantity": str(trade.quantity),
                            "fee": str(trade.fee),
                            "reason": trade.reason,
                        }
                        for trade in result.trades
                    ],
                }
            ],
            "summary": {"status": "full_sample_only", "promotion_decision": "hold"},
        }
        _write_json(args.output, report)
        _record_research(args.research_database, report, output=args.output, stage="backtest")
        print(f"saved={args.output} bars={len(bars)} trades={len(result.trades)} final_equity={result.final_equity} return={result.return_fraction:.4%}")
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
        _record_research(args.research_database, report, output=args.output, stage="walk_forward")
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
        _record_research(args.research_database, report, output=args.output, stage="forward_test")
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
        universe, symbol_aliases = _load_named_universe(parsed_inputs)
        specs = default_theme_specs(args.market)
        if args.benchmark_symbol:
            benchmark_symbol = symbol_aliases.get(args.benchmark_symbol.upper(), args.benchmark_symbol)
            specs = [replace(spec, benchmark_symbol=benchmark_symbol) for spec in specs]
        funding_rates = None
        if args.fundings:
            points = [point for path in args.fundings for point in load_funding_json(path)]
            funding_rates = funding_rates_by_interval(points, args.interval)
            funding_rates = {
                symbol_aliases.get(symbol.upper(), symbol): rates
                for symbol, rates in funding_rates.items()
            }
        leverage = args.max_gross_leverage
        if leverage is None:
            leverage = Decimal("1") if args.market == "spot" else Decimal("2") if args.market == "margin" else Decimal("5")
        margin_interest = args.margin_interest_bps_per_day
        if margin_interest is None:
            margin_interest = Decimal("4") if args.market == "margin" else Decimal("0")
        leverage_map = None
        if args.max_leverage_map:
            try:
                payload = json.loads(args.max_leverage_map.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("max leverage map must be a JSON object")
                leverage_map = {str(symbol): Decimal(str(value)) for symbol, value in payload.items()}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                parser.error(f"invalid --max-leverage-map: {exc}")
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
                margin_interest_bps_per_day=margin_interest,
                rebalance_every_bars=args.rebalance_every_bars,
                max_gross_leverage=leverage,
                max_leverage_by_symbol=leverage_map,
            ),
        )
        _write_json(args.output, report)
        _record_research(args.research_database, report, output=args.output, stage="walk_forward")
        summary = report["summary"]
        print(
            f"saved={args.output} market={args.market} candidates={summary['candidate_count']} "
            f"walk_forward_windows={summary['walk_forward_windows']} status={summary['status']}"
        )
        return

    if args.command == "spike-fade-research":
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
        except ValueError as exc:
            parser.error(str(exc))
        universe, symbol_aliases = _load_named_universe(parsed_inputs)
        funding_rates = None
        if args.fundings:
            points = [point for path in args.fundings for point in load_funding_json(path)]
            funding_rates = funding_rates_by_interval(points, args.interval)
            funding_rates = {
                symbol_aliases.get(symbol.upper(), symbol): rates
                for symbol, rates in funding_rates.items()
            }
        leverage = args.max_gross_leverage or Decimal("2")
        leverage_map = None
        if args.max_leverage_map:
            try:
                payload = json.loads(args.max_leverage_map.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("max leverage map must be a JSON object")
                leverage_map = {str(symbol): Decimal(str(value)) for symbol, value in payload.items()}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                parser.error(f"invalid --max-leverage-map: {exc}")
        report = spike_fade_research_report(
            universe,
            market=args.market,
            specs=default_spike_fade_specs(args.market),
            funding_rates=funding_rates,
            benchmark_symbol=symbol_aliases.get(args.benchmark_symbol.upper(), args.benchmark_symbol) if args.benchmark_symbol else None,
            max_leverage_by_symbol=leverage_map,
            interval=args.interval,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            config=PortfolioConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps if args.fee_bps is not None else (Decimal("5") if args.market == "perpetual" else Decimal("10")),
                slippage_bps=args.slippage_bps if args.slippage_bps is not None else Decimal("5"),
                spread_bps=args.spread_bps if args.spread_bps is not None else Decimal("10"),
                market_impact_bps=args.market_impact_bps if args.market_impact_bps is not None else Decimal("5"),
                margin_interest_bps_per_day=args.margin_interest_bps_per_day if args.margin_interest_bps_per_day is not None else (Decimal("4") if args.market == "margin" else Decimal("0")),
                max_gross_leverage=leverage,
                max_leverage_by_symbol=leverage_map,
            ),
        )
        _write_json(args.output, report)
        _record_research(args.research_database, report, output=args.output, stage="walk_forward")
        summary = report["summary"]
        print(
            f"saved={args.output} market={args.market} candidates={summary['candidate_count']} "
            f"walk_forward_windows={summary['walk_forward_windows']} status={summary['status']}"
        )
        return

    if args.command == "limit-bracket-research":
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
        except ValueError as exc:
            parser.error(str(exc))
        universe, symbol_aliases = _load_named_universe(parsed_inputs)
        funding_rates = None
        if args.fundings:
            points = [point for path in args.fundings for point in load_funding_json(path)]
            funding_rates = funding_rates_by_interval(points, args.interval)
            funding_rates = {symbol_aliases.get(symbol.upper(), symbol): rates for symbol, rates in funding_rates.items()}
        leverage = args.max_gross_leverage
        if leverage is None:
            leverage = Decimal("1") if args.market == "spot" else Decimal("2") if args.market == "margin" else Decimal("5")
        margin_interest = args.margin_interest_bps_per_day
        if margin_interest is None:
            margin_interest = Decimal("4") if args.market == "margin" else Decimal("0")
        leverage_map = None
        if args.max_leverage_map:
            try:
                payload = json.loads(args.max_leverage_map.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("max leverage map must be a JSON object")
                leverage_map = {str(symbol): Decimal(str(value)) for symbol, value in payload.items()}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                parser.error(f"invalid --max-leverage-map: {exc}")
        if args.profile == "mexc-event-v2":
            specs = default_mexc_event_v2_specs(args.market)
        elif args.profile == "mexc-event":
            specs = default_mexc_event_specs(args.market)
        else:
            specs = default_limit_bracket_specs(args.market)
        report = limit_bracket_research_report(
            universe,
            market=args.market,
            specs=specs,
            interval=args.interval,
            funding_rates=funding_rates,
            benchmark_symbol=symbol_aliases.get(args.benchmark_symbol.upper(), args.benchmark_symbol) if args.benchmark_symbol else None,
            max_leverage_by_symbol=leverage_map,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
            minimum_training_trades=args.minimum_training_trades,
            config=PortfolioConfig(
                initial_cash=args.initial_cash,
                fee_bps=args.fee_bps if args.fee_bps is not None else (Decimal("5") if args.market == "perpetual" else Decimal("10")),
                maker_fee_bps=args.maker_fee_bps,
                taker_fee_bps=args.taker_fee_bps,
                slippage_bps=args.slippage_bps if args.slippage_bps is not None else Decimal("5"),
                spread_bps=args.spread_bps if args.spread_bps is not None else Decimal("10"),
                market_impact_bps=args.market_impact_bps if args.market_impact_bps is not None else Decimal("5"),
                adverse_selection_bps=args.adverse_selection_bps,
                stop_gap_penalty_bps=args.stop_gap_penalty_bps,
                margin_interest_bps_per_day=margin_interest,
                max_gross_leverage=leverage,
                max_leverage_by_symbol=leverage_map,
            ),
        )
        _write_json(args.output, report)
        _record_research(args.research_database, report, output=args.output, stage="walk_forward")
        summary = report["summary"]
        print(
            f"saved={args.output} market={args.market} candidates={summary['candidate_count']} "
            f"walk_forward_windows={summary['walk_forward_windows']} status={summary['status']}"
        )
        return

    if args.command == "limit-bracket-signal":
        try:
            parsed_inputs = [_parse_portfolio_input(value) for value in args.inputs]
        except ValueError as exc:
            parser.error(str(exc))
        universe, symbol_aliases = _load_named_universe(parsed_inputs)
        leverage = args.max_gross_leverage
        if leverage is None:
            leverage = Decimal("1") if args.market == "spot" else Decimal("2") if args.market == "margin" else Decimal("5")
        leverage_map = None
        if args.max_leverage_map:
            try:
                payload = json.loads(args.max_leverage_map.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("max leverage map must be a JSON object")
                leverage_map = {str(symbol): Decimal(str(value)) for symbol, value in payload.items()}
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                parser.error(f"invalid --max-leverage-map: {exc}")
        if args.profile in {
            "mexc-short",
            "mexc-long",
            "mexc-short-rejection",
            "mexc-short-v2",
            "mexc-long-v2",
            "mexc-short-rejection-v2",
        }:
            event_specs = (
                default_mexc_event_v2_specs(args.market)
                if args.profile.endswith("-v2")
                else default_mexc_event_specs(args.market)
            )
            spec = {
                "mexc-short": event_specs[0],
                "mexc-long": event_specs[1],
                "mexc-short-rejection": event_specs[2],
                "mexc-short-v2": event_specs[0],
                "mexc-long-v2": event_specs[1],
                "mexc-short-rejection-v2": event_specs[2],
            }[args.profile]
        else:
            profiles = {"fast": 0, "balanced": 1, "deep": 2}
            spec = default_limit_bracket_specs(args.market)[profiles[args.profile]]
        funding_rates = None
        if args.fundings:
            points = [point for path in args.fundings for point in load_funding_json(path)]
            funding_rates = funding_rates_by_interval(points, args.interval)
            funding_rates = {symbol_aliases.get(symbol.upper(), symbol): rates for symbol, rates in funding_rates.items()}
        event = build_limit_bracket_signal_event(
            universe,
            spec,
            interval=args.interval,
            benchmark_symbol=symbol_aliases.get(args.benchmark_symbol.upper(), args.benchmark_symbol) if args.benchmark_symbol else None,
            config=PortfolioConfig(
                max_gross_leverage=leverage,
                max_leverage_by_symbol=leverage_map,
            ),
            funding_rates=funding_rates,
        )
        _write_json(args.output, event)
        print(
            f"saved={args.output} decision={event['decision']} regime={event['regime']['label']} "
            f"candidates={len(event['candidates'])} data_timestamp={event['source']['closed_bar_timestamp']}"
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
        points = (
            MexcContractAdapter().fetch_funding(args.symbol, start=start, end=end)
            if args.exchange == "mexc"
            else HyperliquidAdapter().fetch_funding(args.symbol, start=start, end=end)
        )
        write_funding_json(args.output, points)
        print(f"saved={args.output} points={len(points)}")
        return

    if args.command == "derivatives-shadow":
        venues = args.venue or ["hyperliquid", "mexc", "bybit", "okx"]
        symbols = args.symbol or ["BTC", "ETH"]
        as_of = _parse_utc_datetime(args.as_of) if args.as_of else datetime.now(timezone.utc)
        observations: list[DerivativesObservation] = []
        try:
            if args.history:
                observations.extend(_load_derivatives_observations(args.history))
            if args.input:
                observations.extend(_load_derivatives_observations(args.input))
            else:
                observations.extend(_fetch_derivatives_observations(venues, symbols, as_of))
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            parser.error(f"invalid derivatives input: {exc}")
        if args.database:
            from .duckdb_store import DuckDbDerivativesStore

            store = DuckDbDerivativesStore(args.database)
            store.upsert(observations)
            stored = []
            for symbol in symbols:
                stored.extend(store.load(symbol=symbol, market_type="perpetual"))
            observations = stored
        report = build_derivatives_shadow_report(
            observations,
            as_of=as_of,
            policy=DerivativesFeaturePolicy(min_venues=args.min_venues),
        )
        _write_json(args.output, report)
        labels = ",".join(f"{symbol}={item['label']}" for symbol, item in report["regimes"].items()) or "none"
        print(f"saved={args.output} mode=shadow canonical_strategy_changed=false regimes={labels}")
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
