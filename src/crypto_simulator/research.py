from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite, sqrt
from statistics import mean, median, pstdev
from typing import Any

from .backtest import BacktestConfig, BacktestResult, run_backtest
from .models import OHLCVBar
from .strategy import MultiTimeframeStrategy, SmaCrossStrategy
from .timeframes import interval_duration


_BARS_PER_YEAR = 365 * 24


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """A named, finite candidate. The search never invents parameters."""

    name: str
    execution_fast: int
    execution_slow: int
    trend_fast: int = 5
    trend_slow: int = 20
    regime_fast: int = 5
    regime_slow: int = 20
    single_timeframe: bool = False

    def __post_init__(self) -> None:
        if self.execution_fast <= 0 or self.execution_slow <= self.execution_fast:
            raise ValueError("execution_slow must be greater than execution_fast > 0")
        if not self.single_timeframe:
            if self.trend_fast <= 0 or self.trend_slow <= self.trend_fast:
                raise ValueError("trend_slow must be greater than trend_fast > 0")
            if self.regime_fast <= 0 or self.regime_slow <= self.regime_fast:
                raise ValueError("regime_slow must be greater than regime_fast > 0")

    def build(self):
        if self.single_timeframe:
            return SmaCrossStrategy(self.execution_fast, self.execution_slow)
        return MultiTimeframeStrategy(
            execution_fast=self.execution_fast,
            execution_slow=self.execution_slow,
            trend_fast=self.trend_fast,
            trend_slow=self.trend_slow,
            regime_fast=self.regime_fast,
            regime_slow=self.regime_slow,
        )


def default_strategy_specs() -> list[StrategySpec]:
    """Return a deliberately small baseline grid for the first research pass."""

    return [
        StrategySpec("single_sma_10_30", 10, 30, single_timeframe=True),
        StrategySpec("single_sma_20_50", 20, 50, single_timeframe=True),
        StrategySpec("single_sma_20_80", 20, 80, single_timeframe=True),
        StrategySpec("mtf_8_24_3_12_3_12", 8, 24, 3, 12, 3, 12),
        StrategySpec("mtf_12_36_3_12_5_20", 12, 36, 3, 12, 5, 20),
        StrategySpec("mtf_20_50_5_20_5_20", 20, 50, 5, 20, 5, 20),
        StrategySpec("mtf_20_80_5_20_5_20", 20, 80, 5, 20, 5, 20),
        StrategySpec("mtf_24_72_5_20_5_20", 24, 72, 5, 20, 5, 20),
    ]


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    exposure: float
    turnover: float
    trades: int
    closed_trades: int
    win_rate: float
    benchmark_return: float
    excess_return: float
    beta: float
    alpha: float
    robust_score: float


def _safe_float(value: float) -> float:
    return value if isfinite(value) else 0.0


def dataset_quality(bars: list[OHLCVBar], interval: str = "1hour") -> dict[str, Any]:
    """Describe continuity and duplicate risk before interpreting a report.

    The research engine intentionally does not fill missing candles. This
    summary makes that choice visible so a short or gappy dataset cannot look
    like a clean long-history result by accident.
    """

    duration = interval_duration(interval)
    grouped: dict[tuple[str, str, str], list[OHLCVBar]] = {}
    for bar in bars:
        grouped.setdefault((bar.exchange, bar.symbol, bar.market_type), []).append(bar)

    series_reports: list[dict[str, Any]] = []
    expected_total = 0
    actual_unique_total = 0
    duplicate_total = 0
    gap_total = 0
    largest_gap_hours = 0.0
    for (exchange, symbol, market_type), series in sorted(grouped.items()):
        ordered = sorted(series, key=lambda bar: bar.timestamp)
        timestamps = [bar.timestamp for bar in ordered]
        unique_timestamps = sorted(set(timestamps))
        duplicates = len(timestamps) - len(unique_timestamps)
        if unique_timestamps:
            expected = int((unique_timestamps[-1] - unique_timestamps[0]) / duration) + 1
        else:
            expected = 0
        missing = max(expected - len(unique_timestamps), 0)
        gaps = [
            (current - previous).total_seconds() / 3600.0
            for previous, current in zip(unique_timestamps, unique_timestamps[1:])
            if current - previous > duration
        ]
        gap_count = len(gaps)
        largest_gap = max(gaps, default=0.0)
        expected_total += expected
        actual_unique_total += len(unique_timestamps)
        duplicate_total += duplicates
        gap_total += gap_count
        largest_gap_hours = max(largest_gap_hours, largest_gap)
        series_reports.append(
            {
                "exchange": exchange,
                "symbol": symbol,
                "market_type": market_type,
                "bars": len(unique_timestamps),
                "start": unique_timestamps[0].isoformat() if unique_timestamps else None,
                "end": unique_timestamps[-1].isoformat() if unique_timestamps else None,
                "expected_bars": expected,
                "missing_bars": missing,
                "duplicate_bars": duplicates,
                "gap_count": gap_count,
                "largest_gap_hours": _safe_float(largest_gap),
            }
        )

    coverage = actual_unique_total / expected_total if expected_total else 0.0
    return {
        "interval": interval,
        "series_count": len(series_reports),
        "bars": actual_unique_total,
        "expected_bars": expected_total,
        "missing_bars": max(expected_total - actual_unique_total, 0),
        "duplicate_bars": duplicate_total,
        "gap_count": gap_total,
        "largest_gap_hours": _safe_float(largest_gap_hours),
        "coverage": _safe_float(coverage),
        "contiguous": bool(expected_total and actual_unique_total == expected_total and duplicate_total == 0),
        "series": series_reports,
    }


def _curve_returns(curve: list[tuple[str, Any]]) -> list[float]:
    returns: list[float] = []
    for (_, previous), (_, current) in zip(curve, curve[1:]):
        previous = float(previous)
        current = float(current)
        if previous > 0:
            returns.append(current / previous - 1.0)
    return returns


def _annualized_return(total_return: float, curve: list[tuple[str, Any]]) -> float:
    if len(curve) < 2:
        return total_return
    start = datetime.fromisoformat(curve[0][0]).astimezone(timezone.utc)
    end = datetime.fromisoformat(curve[-1][0]).astimezone(timezone.utc)
    hours = max((end - start).total_seconds() / 3600.0, 1.0)
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (_BARS_PER_YEAR / hours) - 1.0


def _max_drawdown(curve: list[tuple[str, Any]]) -> float:
    peak = 0.0
    drawdown = 0.0
    for _, value in curve:
        equity = float(value)
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, 1.0 - equity / peak)
    return drawdown


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = pstdev(returns)
    return mean(returns) / deviation * sqrt(_BARS_PER_YEAR) if deviation else 0.0


def _sortino(returns: list[float]) -> float:
    if not returns:
        return 0.0
    downside = [min(value, 0.0) ** 2 for value in returns]
    deviation = sqrt(mean(downside))
    return mean(returns) / deviation * sqrt(_BARS_PER_YEAR) if deviation else 0.0


def _beta(strategy_returns: list[float], benchmark_returns: list[float]) -> float:
    pairs = list(zip(strategy_returns, benchmark_returns))
    if len(pairs) < 2:
        return 0.0
    strategy_mean = mean(value[0] for value in pairs)
    benchmark_mean = mean(value[1] for value in pairs)
    covariance = mean((strategy - strategy_mean) * (benchmark - benchmark_mean) for strategy, benchmark in pairs)
    variance = mean((benchmark - benchmark_mean) ** 2 for _, benchmark in pairs)
    return covariance / variance if variance else 0.0


def _closed_trade_stats(result: BacktestResult) -> tuple[int, float]:
    open_trade = None
    wins = 0
    closed = 0
    for trade in result.trades:
        if trade.side == "buy" and open_trade is None:
            open_trade = trade
        elif trade.side == "sell" and open_trade is not None:
            buy_cost = open_trade.price * open_trade.quantity + open_trade.fee
            sell_value = trade.price * trade.quantity - trade.fee
            closed += 1
            wins += int(sell_value > buy_cost)
            open_trade = None
    return closed, wins / closed if closed else 0.0


def benchmark_curve(bars: list[OHLCVBar], config: BacktestConfig) -> list[tuple[str, Decimal]]:
    """Buy-and-hold BTC/JPY using the same entry costs as the strategy."""

    bars = sorted(bars, key=lambda bar: bar.timestamp)
    if not bars:
        return []
    fee_rate = config.fee_bps / Decimal("10000")
    entry_price = config.execution_price(bars[0].open, "buy")
    quantity = config.initial_cash / (entry_price * (Decimal("1") + fee_rate))
    cash = config.initial_cash - quantity * entry_price - quantity * entry_price * fee_rate
    return [(bar.timestamp.isoformat(), cash + quantity * bar.close) for bar in bars]


def evaluate_result(
    result: BacktestResult,
    bars: list[OHLCVBar],
    config: BacktestConfig,
) -> PerformanceMetrics:
    strategy_curve = result.equity_curve
    benchmark = benchmark_curve(bars, config)
    strategy_returns = _curve_returns(strategy_curve)
    benchmark_returns = _curve_returns(benchmark)
    total_return = float(result.final_equity / result.initial_cash - Decimal("1")) if result.initial_cash else 0.0
    benchmark_return = (
        float(benchmark[-1][1] / config.initial_cash - Decimal("1"))
        if benchmark and config.initial_cash
        else 0.0
    )
    annualized = _annualized_return(total_return, strategy_curve)
    max_drawdown = _max_drawdown(strategy_curve)
    closed_trades, win_rate = _closed_trade_stats(result)
    exposure = (
        sum(1 for _, quantity in result.position_curve if Decimal(str(quantity)) > 0) / len(result.position_curve)
        if result.position_curve
        else 0.0
    )
    turnover = (
        sum(float(trade.price * trade.quantity) for trade in result.trades) / float(result.initial_cash)
        if result.initial_cash
        else 0.0
    )
    beta = _beta(strategy_returns, benchmark_returns)
    benchmark_annualized = _annualized_return(benchmark_return, benchmark)
    alpha = annualized - beta * benchmark_annualized
    return PerformanceMetrics(
        total_return=_safe_float(total_return),
        annualized_return=_safe_float(annualized),
        max_drawdown=_safe_float(max_drawdown),
        sharpe=_safe_float(_sharpe(strategy_returns)),
        sortino=_safe_float(_sortino(strategy_returns)),
        calmar=_safe_float(annualized / max_drawdown if max_drawdown else 0.0),
        exposure=_safe_float(exposure),
        turnover=_safe_float(turnover),
        trades=len(result.trades),
        closed_trades=closed_trades,
        win_rate=_safe_float(win_rate),
        benchmark_return=_safe_float(benchmark_return),
        excess_return=_safe_float(total_return - benchmark_return),
        beta=_safe_float(beta),
        alpha=_safe_float(alpha),
        robust_score=_safe_float(total_return - max_drawdown),
    )


def _trade_report(result: BacktestResult) -> list[dict[str, str]]:
    return [
        {
            "timestamp": trade.timestamp,
            "side": trade.side,
            "price": str(trade.price),
            "quantity": str(trade.quantity),
            "fee": str(trade.fee),
            "reason": trade.reason,
        }
        for trade in result.trades
    ]


def forward_test_report(
    bars: list[OHLCVBar],
    spec: StrategySpec,
    config: BacktestConfig | None = None,
    *,
    holdout_days: int = 30,
    interval: str = "1hour",
) -> dict[str, Any]:
    """Evaluate one frozen strategy on the latest unseen holdout window.

    Unlike candidate search, this function never chooses a strategy. The
    caller must provide the already-frozen ``StrategySpec``. All candles
    before the holdout are indicator warm-up only; trades and the reported
    equity curve begin at the first holdout candle. This makes the output a
    forward-test evidence record rather than another full-sample optimization.
    """

    if holdout_days <= 0:
        raise ValueError("holdout_days must be positive")
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    if len(bars) < 2:
        raise ValueError("at least two bars are required for a forward test")
    config = config or BacktestConfig()
    requested_start = bars[-1].timestamp - timedelta(days=holdout_days)
    start_index = next(
        (index for index, bar in enumerate(bars) if bar.timestamp >= requested_start),
        len(bars),
    )
    if start_index <= 0 or start_index >= len(bars):
        raise ValueError("dataset does not contain a separate forward holdout window")
    holdout_bars = bars[start_index:]
    result = run_backtest(bars, spec.build(), config, start_index=start_index)
    metrics = evaluate_result(result, holdout_bars, config)
    quality = dataset_quality(bars, interval)
    return {
        "dataset": {
            "bars": len(bars),
            "start": bars[0].timestamp.isoformat(),
            "end": bars[-1].timestamp.isoformat(),
            "interval": interval,
            "exchange": bars[0].exchange,
            "symbol": bars[0].symbol,
            "quality": quality,
        },
        "holdout": {
            "requested_days": holdout_days,
            "warmup_bars": start_index,
            "bars": len(holdout_bars),
            "start": holdout_bars[0].timestamp.isoformat(),
            "end": holdout_bars[-1].timestamp.isoformat(),
        },
        "method": {
            "mode": "frozen_forward_test",
            "costs": {
                "fee_bps": str(config.fee_bps),
                "slippage_bps": str(config.slippage_bps),
                "spread_bps": str(config.spread_bps),
                "market_impact_bps": str(config.market_impact_bps),
                "one_way_execution_bps": str(config.one_way_execution_bps),
            },
            "execution": "signal on the previous closed candle, fill at the next candle open",
        },
        "strategy": asdict(spec),
        "metrics": asdict(metrics),
        "trades": _trade_report(result),
        "status": "forward_test_only",
        "promotion_gate": "manual_review_required",
    }


@dataclass(frozen=True, slots=True)
class StrategyEvaluation:
    spec: StrategySpec
    metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_strategy: str
    train_metrics: PerformanceMetrics
    test_metrics: PerformanceMetrics


def evaluate_candidates(
    bars: list[OHLCVBar],
    specs: list[StrategySpec] | None = None,
    config: BacktestConfig | None = None,
) -> list[StrategyEvaluation]:
    config = config or BacktestConfig()
    specs = specs or default_strategy_specs()
    return [
        StrategyEvaluation(spec, evaluate_result(run_backtest(bars, spec.build(), config), bars, config))
        for spec in specs
    ]


def walk_forward_search(
    bars: list[OHLCVBar],
    specs: list[StrategySpec] | None = None,
    config: BacktestConfig | None = None,
    *,
    train_days: int = 180,
    test_days: int = 30,
    step_days: int = 30,
) -> list[WalkForwardWindow]:
    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        raise ValueError("walk-forward periods must be positive")
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    if not bars:
        return []
    config = config or BacktestConfig()
    specs = specs or default_strategy_specs()
    timestamps = [bar.timestamp for bar in bars]
    cursor = timestamps[0] + timedelta(days=train_days)
    windows: list[WalkForwardWindow] = []
    final_timestamp = timestamps[-1]
    while cursor + timedelta(days=test_days) <= final_timestamp:
        train_end_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= cursor), len(bars))
        test_end_time = cursor + timedelta(days=test_days)
        test_end_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= test_end_time), len(bars))
        if train_end_index == 0 or test_end_index <= train_end_index:
            break
        train_bars = bars[:train_end_index]
        test_bars = bars[train_end_index:test_end_index]
        train_evaluations = evaluate_candidates(train_bars, specs, config)
        selected = max(
            train_evaluations,
            key=lambda evaluation: (
                evaluation.metrics.robust_score,
                evaluation.metrics.excess_return,
                -evaluation.metrics.max_drawdown,
            ),
        )
        test_result = run_backtest(
            bars[:test_end_index],
            selected.spec.build(),
            config,
            start_index=train_end_index,
        )
        windows.append(
            WalkForwardWindow(
                train_start=train_bars[0].timestamp.isoformat(),
                train_end=train_bars[-1].timestamp.isoformat(),
                test_start=test_bars[0].timestamp.isoformat(),
                test_end=test_bars[-1].timestamp.isoformat(),
                selected_strategy=selected.spec.name,
                train_metrics=selected.metrics,
                test_metrics=evaluate_result(test_result, test_bars, config),
            )
        )
        cursor += timedelta(days=step_days)
    return windows


def research_report(
    bars: list[OHLCVBar],
    specs: list[StrategySpec] | None = None,
    config: BacktestConfig | None = None,
    *,
    interval: str = "1hour",
    train_days: int = 180,
    test_days: int = 30,
    step_days: int = 30,
) -> dict[str, Any]:
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    specs = specs or default_strategy_specs()
    config = config or BacktestConfig()
    full_sample = evaluate_candidates(bars, specs, config) if bars else []
    windows = walk_forward_search(
        bars,
        specs,
        config,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    quality = dataset_quality(bars, interval)
    positive_oos = [window.test_metrics.excess_return > 0 for window in windows]
    positive_oos_windows = sum(positive_oos)
    negative_oos_windows = len(positive_oos) - positive_oos_windows
    positive_oos_fraction = mean(positive_oos) if positive_oos else None
    median_oos_excess = (
        median(window.test_metrics.excess_return for window in windows)
        if windows
        else None
    )
    if not windows:
        validation_status = "insufficient_history_for_walk_forward"
    elif positive_oos_fraction is not None and positive_oos_fraction < 0.6:
        validation_status = "not_validated"
    elif median_oos_excess is not None and median_oos_excess <= 0:
        validation_status = "not_validated"
    elif len(windows) < 6:
        validation_status = "low_statistical_power"
    else:
        validation_status = "candidate_requires_forward_test"
    return {
        "dataset": {
            "bars": len(bars),
            "start": bars[0].timestamp.isoformat() if bars else None,
            "end": bars[-1].timestamp.isoformat() if bars else None,
            "interval": interval,
            "exchange": bars[0].exchange if bars else None,
            "symbol": bars[0].symbol if bars else None,
            "quality": quality,
        },
        "method": {
            "train_days": train_days,
            "test_days": test_days,
            "step_days": step_days,
            "costs": {
                "fee_bps": str(config.fee_bps),
                "slippage_bps": str(config.slippage_bps),
                "spread_bps": str(config.spread_bps),
                "market_impact_bps": str(config.market_impact_bps),
                "one_way_execution_bps": str(config.one_way_execution_bps),
            },
            "selection": "train robust_score = total_return - max_drawdown; ties prefer benchmark excess return and lower drawdown",
        },
        "full_sample": [
            {"strategy": asdict(evaluation.spec), "metrics": asdict(evaluation.metrics)}
            for evaluation in sorted(full_sample, key=lambda item: item.metrics.robust_score, reverse=True)
        ],
        "walk_forward": [
            {
                "train_start": window.train_start,
                "train_end": window.train_end,
                "test_start": window.test_start,
                "test_end": window.test_end,
                "selected_strategy": window.selected_strategy,
                "train_metrics": asdict(window.train_metrics),
                "test_metrics": asdict(window.test_metrics),
            }
            for window in windows
        ],
        "summary": {
            "candidate_count": len(specs),
            "walk_forward_windows": len(windows),
            "positive_oos_excess_fraction": positive_oos_fraction,
            "positive_oos_windows": positive_oos_windows,
            "negative_oos_windows": negative_oos_windows,
            "median_oos_excess_return": median_oos_excess,
            "data_quality_status": "complete" if quality["contiguous"] else "gaps_or_duplicates_detected",
            "validation_rule": {
                "minimum_windows": 6,
                "minimum_positive_oos_excess_fraction": 0.6,
                "minimum_median_oos_excess_return": 0.0,
            },
            "status": validation_status,
        },
    }
