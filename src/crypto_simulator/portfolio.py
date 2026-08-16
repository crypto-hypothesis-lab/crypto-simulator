from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite, sqrt
from statistics import mean, median, pstdev
from typing import Any, Mapping

from .models import OHLCVBar
from .research import dataset_quality
from .timeframes import interval_duration


def _safe_float(value: float) -> float:
    return value if isfinite(value) else 0.0


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _sma(values: list[Decimal], window: int) -> Decimal:
    if len(values) < window:
        raise ValueError("not enough values for moving average")
    return sum(values[-window:], Decimal("0")) / Decimal(window)


def _return(values: list[Decimal], lookback: int) -> float:
    if len(values) <= lookback or values[-1 - lookback] <= 0:
        return 0.0
    return float(values[-1] / values[-1 - lookback] - Decimal("1"))


def _mean_decimal(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _percentile_rank(value: float, values: list[float]) -> float:
    if len(values) <= 1:
        return 0.5
    return sum(candidate < value for candidate in values) / (len(values) - 1)


def _realized_volatility(closes: list[Decimal], window: int = 20) -> float:
    returns = [_return(closes[: index + 1], 1) for index in range(1, len(closes))]
    return _safe_float(pstdev(returns[-window:]) if len(returns) >= 2 else 0.0)


def _curve_returns(curve: list[tuple[str, Decimal]]) -> list[float]:
    result: list[float] = []
    for (_, previous), (_, current) in zip(curve, curve[1:]):
        previous_value = float(previous)
        if previous_value > 0:
            result.append(float(current) / previous_value - 1.0)
    return result


def _annualized_return(total_return: float, curve: list[tuple[str, Decimal]]) -> float:
    if len(curve) < 2:
        return total_return
    start = datetime.fromisoformat(curve[0][0]).astimezone(timezone.utc)
    end = datetime.fromisoformat(curve[-1][0]).astimezone(timezone.utc)
    days = max((end - start).total_seconds() / 86_400.0, 1.0 / 24.0)
    if total_return <= -1.0:
        return -1.0
    return (1.0 + total_return) ** (365.0 / days) - 1.0


def _max_drawdown(curve: list[tuple[str, Decimal]]) -> float:
    peak = 0.0
    result = 0.0
    for _, value in curve:
        equity = float(value)
        peak = max(peak, equity)
        if peak > 0:
            result = max(result, 1.0 - equity / peak)
    return result


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = pstdev(returns)
    return mean(returns) / deviation * sqrt(365.0) if deviation else 0.0


def _sortino(returns: list[float]) -> float:
    if not returns:
        return 0.0
    downside = [min(value, 0.0) ** 2 for value in returns]
    deviation = sqrt(mean(downside))
    return mean(returns) / deviation * sqrt(365.0) if deviation else 0.0


@dataclass(frozen=True, slots=True)
class FundingPoint:
    """One HyperLiquid funding observation.

    ``rate`` is the rate applied for the interval ending at ``timestamp``.
    The portfolio backtester expects rates to be aligned to its price bars;
    callers may aggregate the hourly observations to daily bars first.
    """

    exchange: str
    symbol: str
    timestamp: datetime
    rate: Decimal
    premium: Decimal | None = None

    def __post_init__(self) -> None:
        timestamp = self.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        object.__setattr__(self, "timestamp", timestamp.astimezone(timezone.utc))
        object.__setattr__(self, "rate", Decimal(str(self.rate)))
        if self.premium is not None:
            object.__setattr__(self, "premium", Decimal(str(self.premium)))

    @property
    def timestamp_ms(self) -> int:
        return int(self.timestamp.timestamp() * 1000)


def funding_rates_by_interval(
    points: list[FundingPoint],
    interval: str,
) -> dict[str, dict[datetime, Decimal]]:
    """Aggregate hourly funding observations into price-bar buckets."""

    duration = interval_duration(interval)
    duration_seconds = int(duration.total_seconds())
    result: dict[str, dict[datetime, Decimal]] = {}
    for point in points:
        epoch = int(point.timestamp.timestamp())
        bucket = datetime.fromtimestamp(
            epoch - epoch % duration_seconds,
            tz=timezone.utc,
        )
        result.setdefault(point.symbol, {})[bucket] = (
            result.setdefault(point.symbol, {}).get(bucket, Decimal("0")) + point.rate
        )
    return result


@dataclass(frozen=True, slots=True)
class ThemeMomentumSpec:
    """Finite, auditable candidate for cross-sectional theme momentum.

    A theme is intentionally represented by relative strength, momentum, and
    volume acceleration instead of a hand-maintained narrative label. This
    avoids changing historical labels after the fact while still capturing
    capital rotating into a group of coins.
    """

    name: str
    market: str = "spot"
    momentum_fast: int = 7
    momentum_slow: int = 30
    regime_fast: int = 20
    regime_slow: int = 100
    top_n: int = 1
    bottom_n: int = 1
    breadth_threshold: float = 0.5
    long_exposure: float = 0.8
    short_exposure: float = 0.0
    risk_off_short: bool = False
    benchmark_symbol: str | None = None
    max_leverage: float = 1.0
    risk_off_max_leverage: float = 1.0

    def __post_init__(self) -> None:
        if self.market not in {"spot", "perpetual"}:
            raise ValueError("market must be spot or perpetual")
        for name in ("momentum_fast", "momentum_slow", "regime_fast", "regime_slow", "top_n", "bottom_n"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.momentum_slow <= self.momentum_fast:
            raise ValueError("momentum_slow must exceed momentum_fast")
        if self.regime_slow <= self.regime_fast:
            raise ValueError("regime_slow must exceed regime_fast")
        if not 0.0 <= self.breadth_threshold <= 1.0:
            raise ValueError("breadth_threshold must be between 0 and 1")
        if self.long_exposure < 0 or self.short_exposure < 0:
            raise ValueError("exposure values must not be negative")
        if self.market == "spot" and self.short_exposure:
            raise ValueError("spot candidates cannot have short exposure")
        if self.max_leverage <= 0 or self.risk_off_max_leverage <= 0:
            raise ValueError("leverage limits must be positive")
        if self.market == "spot" and self.max_leverage > 1:
            raise ValueError("spot candidates cannot use leverage")

    @property
    def minimum_history(self) -> int:
        return max(self.momentum_slow, self.regime_slow, self.momentum_fast) + 1


def default_theme_specs(market: str) -> list[ThemeMomentumSpec]:
    """Return a small pre-declared grid for one venue type."""

    if market == "spot":
        return [
            ThemeMomentumSpec("spot_theme_7_30_20_100_top1", market="spot", momentum_fast=7, momentum_slow=30, top_n=1, long_exposure=0.85),
            ThemeMomentumSpec("spot_theme_14_42_20_100_top1", market="spot", momentum_fast=14, momentum_slow=42, top_n=1, long_exposure=0.85),
            ThemeMomentumSpec("spot_theme_21_63_20_120_top2", market="spot", momentum_fast=21, momentum_slow=63, regime_slow=120, top_n=2, long_exposure=0.85),
        ]
    if market == "perpetual":
        return [
            ThemeMomentumSpec("perp_regime_momo_7_30_top1", market="perpetual", momentum_fast=7, momentum_slow=30, top_n=1, bottom_n=1, long_exposure=0.70, short_exposure=0.30, risk_off_short=True, max_leverage=5.0, risk_off_max_leverage=2.0),
            ThemeMomentumSpec("perp_regime_momo_14_42_top2", market="perpetual", momentum_fast=14, momentum_slow=42, top_n=2, bottom_n=2, long_exposure=0.65, short_exposure=0.35, risk_off_short=True, max_leverage=5.0, risk_off_max_leverage=2.0),
            ThemeMomentumSpec("perp_regime_momo_21_63_top2", market="perpetual", momentum_fast=21, momentum_slow=63, regime_slow=120, top_n=2, bottom_n=2, long_exposure=0.60, short_exposure=0.40, risk_off_short=True, max_leverage=5.0, risk_off_max_leverage=2.0),
        ]
    raise ValueError("market must be spot or perpetual")


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    target_weights: dict[str, float]
    regime: str
    breadth: float
    scores: dict[str, float]
    long_symbols: tuple[str, ...] = ()
    short_symbols: tuple[str, ...] = ()
    confidence: float = 0.0
    leverage: float = 0.0


class ThemeMomentumStrategy:
    """Regime-gated cross-sectional momentum with a theme-momentum proxy."""

    def __init__(self, spec: ThemeMomentumSpec) -> None:
        self.spec = spec

    @staticmethod
    def _resolve_benchmark(symbols: list[str], requested: str | None) -> str:
        if requested and requested in symbols:
            return requested
        btc_symbols = [symbol for symbol in symbols if "BTC" in symbol.upper()]
        if btc_symbols:
            return sorted(btc_symbols)[0]
        raise ValueError("a BTC benchmark symbol is required for market regime")

    def decision(self, history: Mapping[str, list[OHLCVBar]]) -> PortfolioDecision:
        symbols = sorted(history)
        benchmark = self._resolve_benchmark(symbols, self.spec.benchmark_symbol)
        minimum = self.spec.minimum_history
        usable = {
            symbol: series
            for symbol, series in history.items()
            if len(series) >= minimum
        }
        if benchmark not in usable:
            return PortfolioDecision({}, "warmup", 0.0, {})

        benchmark_closes = [bar.close for bar in usable[benchmark]]
        benchmark_fast = _sma(benchmark_closes, self.spec.regime_fast)
        benchmark_slow = _sma(benchmark_closes, self.spec.regime_slow)
        breadth_assets = [symbol for symbol in usable if symbol != benchmark]
        breadth_checks = []
        for symbol in breadth_assets:
            closes = [bar.close for bar in usable[symbol]]
            breadth_checks.append(closes[-1] > _sma(closes, self.spec.regime_fast))
        breadth = sum(breadth_checks) / len(breadth_checks) if breadth_checks else 1.0
        risk_on = (
            benchmark_closes[-1] > benchmark_slow
            and benchmark_fast > benchmark_slow
            and breadth >= self.spec.breadth_threshold
        )
        regime = "risk_on" if risk_on else "risk_off"

        metrics: dict[str, tuple[float, float, float]] = {}
        for symbol, series in usable.items():
            closes = [bar.close for bar in series]
            volumes = [bar.volume for bar in series]
            fast_return = _return(closes, self.spec.momentum_fast)
            slow_return = _return(closes, self.spec.momentum_slow)
            benchmark_return = _return(benchmark_closes, self.spec.momentum_slow)
            recent_volume = _mean_decimal(volumes[-self.spec.momentum_fast:])
            older_volume = _mean_decimal(volumes[-self.spec.momentum_slow:-self.spec.momentum_fast])
            volume_acceleration = float(recent_volume / older_volume - Decimal("1")) if older_volume > 0 else 0.0
            metrics[symbol] = (fast_return, slow_return - benchmark_return, volume_acceleration)

        fast_values = [values[0] for values in metrics.values()]
        relative_values = [values[1] for values in metrics.values()]
        volume_values = [values[2] for values in metrics.values()]
        scores = {
            symbol: _safe_float(
                0.35 * _percentile_rank(values[0], fast_values)
                + 0.45 * _percentile_rank(values[1], relative_values)
                + 0.20 * _percentile_rank(values[2], volume_values)
            )
            for symbol, values in metrics.items()
        }
        regime_strength = _clamp(abs(float(benchmark_fast / benchmark_slow - Decimal("1"))) / 0.05)
        breadth_confidence = breadth if risk_on else 1.0 - breadth
        score_spread = max(scores.values(), default=0.0) - min(scores.values(), default=0.0)
        theme_confidence = _clamp(score_spread / 0.25)
        volatility_factor = _clamp(0.06 / (0.06 + _realized_volatility(benchmark_closes)), 0.35, 1.0)
        confidence = _clamp(
            (0.45 * regime_strength + 0.35 * breadth_confidence + 0.20 * theme_confidence)
            * volatility_factor
        )
        if self.spec.market == "perpetual":
            if risk_on:
                leverage = 1.0 + (self.spec.max_leverage - 1.0) * confidence
            elif self.spec.risk_off_short:
                leverage = 0.5 + (self.spec.risk_off_max_leverage - 0.5) * confidence
            else:
                leverage = 0.0
        else:
            leverage = 1.0 if risk_on else 0.0
        ranked = sorted(scores, key=lambda symbol: (scores[symbol], symbol), reverse=True)
        reverse_ranked = list(reversed(ranked))
        longs = tuple(ranked[: self.spec.top_n])
        shorts = tuple(symbol for symbol in reverse_ranked if symbol not in longs)[: self.spec.bottom_n]
        weights: dict[str, float] = {}

        if self.spec.market == "spot":
            if risk_on and longs:
                weight = self.spec.long_exposure / len(longs)
                weights = {symbol: weight for symbol in longs}
        elif risk_on:
            if longs:
                weight = self.spec.long_exposure * leverage / len(longs)
                weights.update({symbol: weight for symbol in longs})
            if shorts and self.spec.short_exposure:
                weight = -self.spec.short_exposure * leverage / len(shorts)
                weights.update({symbol: weight for symbol in shorts})
        elif self.spec.risk_off_short and shorts:
            weight = -self.spec.short_exposure * leverage / len(shorts)
            weights.update({symbol: weight for symbol in shorts})

        return PortfolioDecision(weights, regime, _safe_float(breadth), scores, longs, shorts, confidence, leverage)


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    initial_cash: Decimal = Decimal("100000")
    fee_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("5")
    spread_bps: Decimal = Decimal("0")
    market_impact_bps: Decimal = Decimal("0")
    rebalance_every_bars: int = 1
    max_gross_leverage: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in (
            "initial_cash",
            "fee_bps",
            "slippage_bps",
            "spread_bps",
            "market_impact_bps",
            "max_gross_leverage",
        ):
            object.__setattr__(self, name, Decimal(str(getattr(self, name))))
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if self.rebalance_every_bars <= 0:
            raise ValueError("rebalance_every_bars must be positive")
        if self.max_gross_leverage <= 0:
            raise ValueError("max_gross_leverage must be positive")
        for name in ("fee_bps", "slippage_bps", "spread_bps", "market_impact_bps"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")

    @property
    def one_way_execution_bps(self) -> Decimal:
        return self.slippage_bps + self.spread_bps / Decimal("2") + self.market_impact_bps

    def execution_price(self, open_price: Decimal, side: str) -> Decimal:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        rate = self.one_way_execution_bps / Decimal("10000")
        return open_price * (Decimal("1") + rate if side == "buy" else Decimal("1") - rate)


@dataclass(frozen=True, slots=True)
class PortfolioTrade:
    timestamp: str
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    notional: Decimal
    fee: Decimal
    reason: str


@dataclass(slots=True)
class PortfolioResult:
    initial_cash: Decimal
    final_equity: Decimal
    cash: Decimal
    positions: dict[str, Decimal]
    trades: list[PortfolioTrade] = field(default_factory=list)
    equity_curve: list[tuple[str, Decimal]] = field(default_factory=list)
    benchmark_curve: list[tuple[str, Decimal]] = field(default_factory=list)
    gross_exposure_curve: list[tuple[str, float]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    funding_cost: Decimal = Decimal("0")


def _normalise_universe(universe: Mapping[str, list[OHLCVBar]]) -> tuple[dict[str, list[OHLCVBar]], list[datetime]]:
    if not universe:
        raise ValueError("at least one asset series is required")
    normalised = {
        symbol: sorted(series, key=lambda bar: bar.timestamp)
        for symbol, series in universe.items()
        if series
    }
    if not normalised:
        raise ValueError("at least one non-empty asset series is required")
    timestamp_sets = [set(bar.timestamp for bar in series) for series in normalised.values()]
    common = set.intersection(*timestamp_sets)
    if len(common) < 2:
        raise ValueError("asset series do not share at least two timestamps")
    return normalised, sorted(common)


def _trade_order(symbol: str, delta: Decimal, current: Decimal) -> tuple[int, str]:
    closing = current != 0 and current * delta < 0
    return (0 if closing else 1, symbol)


def _cap_target_weights(target_weights: Mapping[str, float], max_gross_leverage: Decimal) -> dict[str, float]:
    gross = sum(abs(weight) for weight in target_weights.values())
    cap = float(max_gross_leverage)
    if gross <= cap or gross == 0:
        return dict(target_weights)
    scale = cap / gross
    return {symbol: weight * scale for symbol, weight in target_weights.items()}


def _execute_target(
    *,
    timestamp: datetime,
    current_bars: Mapping[str, OHLCVBar],
    target_weights: Mapping[str, float],
    positions: dict[str, Decimal],
    cash: Decimal,
    equity: Decimal,
    config: PortfolioConfig,
    reason: str,
    trades: list[PortfolioTrade],
) -> Decimal:
    targets = {
        symbol: equity * Decimal(str(weight)) / current_bars[symbol].open
        for symbol, weight in target_weights.items()
        if symbol in current_bars
    }
    deltas = {
        symbol: targets.get(symbol, Decimal("0")) - positions.get(symbol, Decimal("0"))
        for symbol in current_bars
    }
    for symbol, delta in sorted(deltas.items(), key=lambda item: _trade_order(item[0], item[1], positions.get(item[0], Decimal("0")))):
        if delta == 0:
            continue
        side = "buy" if delta > 0 else "sell"
        price = config.execution_price(current_bars[symbol].open, side)
        notional = abs(delta * price)
        fee = notional * config.fee_bps / Decimal("10000")
        cash -= delta * price
        cash -= fee
        positions[symbol] = positions.get(symbol, Decimal("0")) + delta
        trades.append(
            PortfolioTrade(timestamp.isoformat(), symbol, side, price, abs(delta), notional, fee, reason)
        )
    gross = sum(abs(positions[symbol] * current_bars[symbol].open) for symbol in positions)
    if equity > 0 and gross / equity > config.max_gross_leverage + Decimal("0.000001"):
        raise ValueError("target weights exceed max_gross_leverage")
    return cash


def _benchmark_curve(
    bars: list[OHLCVBar],
    start_index: int,
    config: PortfolioConfig,
) -> list[tuple[str, Decimal]]:
    if start_index >= len(bars):
        return []
    fee_rate = config.fee_bps / Decimal("10000")
    entry = config.execution_price(bars[start_index].open, "buy")
    quantity = config.initial_cash / (entry * (Decimal("1") + fee_rate))
    cash = config.initial_cash - quantity * entry - quantity * entry * fee_rate
    return [
        (bar.timestamp.isoformat(), cash + quantity * bar.close)
        for bar in bars[start_index:]
    ]


def run_portfolio_backtest(
    universe: Mapping[str, list[OHLCVBar]],
    strategy: ThemeMomentumStrategy,
    config: PortfolioConfig | None = None,
    *,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    start_index: int = 0,
) -> PortfolioResult:
    """Run a synchronized, next-bar-open portfolio backtest.

    Signals use only each asset's history through the current close. The
    resulting target weights are executed at the next common bar's open.
    Negative weights are allowed only for perpetual candidates. Funding rates
    are applied to the position held at the bar timestamp.
    """

    config = config or PortfolioConfig()
    normalised, timestamps = _normalise_universe(universe)
    if start_index < 0 or start_index >= len(timestamps):
        raise ValueError("start_index must refer to a common timestamp")
    by_timestamp = {
        symbol: {bar.timestamp: bar for bar in series}
        for symbol, series in normalised.items()
    }
    positions = {symbol: Decimal("0") for symbol in normalised}
    history = {symbol: [] for symbol in normalised}
    trades: list[PortfolioTrade] = []
    equity_curve: list[tuple[str, Decimal]] = []
    gross_exposure_curve: list[tuple[str, float]] = []
    decisions: list[dict[str, Any]] = []
    cash = config.initial_cash
    funding_cost = Decimal("0")
    pending = PortfolioDecision({}, "warmup", 0.0, {})

    for index, timestamp in enumerate(timestamps):
        current = {symbol: by_timestamp[symbol][timestamp] for symbol in normalised}
        if index > start_index and funding_rates:
            for symbol, position in positions.items():
                rate = funding_rates.get(symbol, {}).get(timestamp, Decimal("0"))
                payment = position * current[symbol].open * Decimal(str(rate))
                cash -= payment
                funding_cost += payment
        if index < start_index:
            for symbol in normalised:
                history[symbol].append(current[symbol])
            pending = strategy.decision(history)
            continue

        if index == start_index and start_index == 0:
            pending = PortfolioDecision({}, "warmup", 0.0, {})
        if (index - start_index) % config.rebalance_every_bars == 0:
            equity_at_open = cash + sum(positions[symbol] * current[symbol].open for symbol in positions)
            cash = _execute_target(
                timestamp=timestamp,
                current_bars=current,
                target_weights=_cap_target_weights(pending.target_weights, config.max_gross_leverage),
                positions=positions,
                cash=cash,
                equity=equity_at_open,
                config=config,
                reason=f"{pending.regime}_theme_rebalance",
                trades=trades,
            )
        for symbol in normalised:
            history[symbol].append(current[symbol])
        equity = cash + sum(positions[symbol] * current[symbol].close for symbol in positions)
        gross_exposure = (
            sum(abs(positions[symbol] * current[symbol].close) for symbol in positions) / equity
            if equity > 0
            else 0
        )
        equity_curve.append((timestamp.isoformat(), equity))
        gross_exposure_curve.append((timestamp.isoformat(), _safe_float(float(gross_exposure))))
        decision = strategy.decision(history)
        pending = decision
        decisions.append(
            {
                "timestamp": timestamp.isoformat(),
                "regime": decision.regime,
                "breadth": decision.breadth,
                "long_symbols": list(decision.long_symbols),
                "short_symbols": list(decision.short_symbols),
                "target_weights": decision.target_weights,
                "confidence": decision.confidence,
                "leverage": decision.leverage,
            }
        )

    final_timestamp = timestamps[-1]
    final_bars = {symbol: by_timestamp[symbol][final_timestamp] for symbol in normalised}
    final_equity = cash + sum(positions[symbol] * final_bars[symbol].close for symbol in positions)
    benchmark_symbol = strategy._resolve_benchmark(sorted(normalised), strategy.spec.benchmark_symbol)
    benchmark_bars = [by_timestamp[benchmark_symbol][timestamp] for timestamp in timestamps]
    benchmark = _benchmark_curve(benchmark_bars, start_index, config)
    return PortfolioResult(
        config.initial_cash,
        final_equity,
        cash,
        positions,
        trades,
        equity_curve,
        benchmark,
        gross_exposure_curve,
        decisions,
        funding_cost,
    )


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    exposure: float
    max_gross_exposure: float
    turnover: float
    trades: int
    benchmark_return: float
    excess_return: float
    funding_cost_fraction: float
    robust_score: float


def evaluate_portfolio_result(result: PortfolioResult) -> PortfolioMetrics:
    strategy_curve = result.equity_curve
    benchmark = result.benchmark_curve
    total_return = float(result.final_equity / result.initial_cash - Decimal("1"))
    benchmark_return = (
        float(benchmark[-1][1] / result.initial_cash - Decimal("1"))
        if benchmark
        else 0.0
    )
    exposure_values = [value for _, value in result.gross_exposure_curve]
    turnover = (
        sum(float(trade.notional) for trade in result.trades) / float(result.initial_cash)
        if result.initial_cash
        else 0.0
    )
    funding_fraction = float(result.funding_cost / result.initial_cash)
    drawdown = _max_drawdown(strategy_curve)
    return PortfolioMetrics(
        total_return=_safe_float(total_return),
        annualized_return=_safe_float(_annualized_return(total_return, strategy_curve)),
        max_drawdown=_safe_float(drawdown),
        sharpe=_safe_float(_sharpe(_curve_returns(strategy_curve))),
        sortino=_safe_float(_sortino(_curve_returns(strategy_curve))),
        exposure=_safe_float(mean(exposure_values) if exposure_values else 0.0),
        max_gross_exposure=_safe_float(max(exposure_values, default=0.0)),
        turnover=_safe_float(turnover),
        trades=len(result.trades),
        benchmark_return=_safe_float(benchmark_return),
        excess_return=_safe_float(total_return - benchmark_return),
        funding_cost_fraction=_safe_float(funding_fraction),
        robust_score=_safe_float(total_return - drawdown),
    )


@dataclass(frozen=True, slots=True)
class PortfolioWalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_strategy: str
    train_metrics: PortfolioMetrics
    test_metrics: PortfolioMetrics


def _slice_universe(universe: Mapping[str, list[OHLCVBar]], end_timestamp: datetime) -> dict[str, list[OHLCVBar]]:
    return {
        symbol: [bar for bar in bars if bar.timestamp < end_timestamp]
        for symbol, bars in universe.items()
    }


def portfolio_walk_forward_search(
    universe: Mapping[str, list[OHLCVBar]],
    specs: list[ThemeMomentumSpec],
    config: PortfolioConfig,
    *,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
) -> list[PortfolioWalkForwardWindow]:
    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        raise ValueError("walk-forward periods must be positive")
    _, timestamps = _normalise_universe(universe)
    windows: list[PortfolioWalkForwardWindow] = []
    cursor = timestamps[0] + timedelta(days=train_days)
    while cursor + timedelta(days=test_days) <= timestamps[-1]:
        train_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= cursor), len(timestamps))
        test_end = cursor + timedelta(days=test_days)
        test_end_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= test_end), len(timestamps))
        if train_index <= 0 or test_end_index <= train_index:
            break
        train_universe = _slice_universe(universe, cursor)
        train_evaluations = [
            (spec, evaluate_portfolio_result(run_portfolio_backtest(train_universe, ThemeMomentumStrategy(spec), config, funding_rates=funding_rates)))
            for spec in specs
        ]
        selected_spec, selected_metrics = max(
            train_evaluations,
            key=lambda item: (item[1].robust_score, item[1].excess_return, -item[1].max_drawdown),
        )
        test_timestamps = timestamps[train_index:test_end_index]
        test_universe = {
            symbol: [bar for bar in bars if bar.timestamp <= test_timestamps[-1]]
            for symbol, bars in universe.items()
        }
        test_result = run_portfolio_backtest(
            test_universe,
            ThemeMomentumStrategy(selected_spec),
            config,
            funding_rates=funding_rates,
            start_index=train_index,
        )
        windows.append(
            PortfolioWalkForwardWindow(
                timestamps[0].isoformat(),
                timestamps[train_index - 1].isoformat(),
                test_timestamps[0].isoformat(),
                test_timestamps[-1].isoformat(),
                selected_spec.name,
                selected_metrics,
                evaluate_portfolio_result(test_result),
            )
        )
        cursor += timedelta(days=step_days)
    return windows


def _trade_report(result: PortfolioResult) -> list[dict[str, str]]:
    return [
        {
            "timestamp": trade.timestamp,
            "symbol": trade.symbol,
            "side": trade.side,
            "price": str(trade.price),
            "quantity": str(trade.quantity),
            "notional": str(trade.notional),
            "fee": str(trade.fee),
            "reason": trade.reason,
        }
        for trade in result.trades
    ]


def portfolio_research_report(
    universe: Mapping[str, list[OHLCVBar]],
    *,
    market: str,
    specs: list[ThemeMomentumSpec] | None = None,
    config: PortfolioConfig | None = None,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    interval: str = "1day",
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
) -> dict[str, Any]:
    if market not in {"spot", "perpetual"}:
        raise ValueError("market must be spot or perpetual")
    config = config or PortfolioConfig(max_gross_leverage=Decimal("1") if market == "spot" else Decimal("5"))
    specs = specs or default_theme_specs(market)
    if any(spec.market != market for spec in specs):
        raise ValueError("all strategy specs must match market")
    normalised, timestamps = _normalise_universe(universe)
    evaluations = []
    for spec in specs:
        result = run_portfolio_backtest(normalised, ThemeMomentumStrategy(spec), config, funding_rates=funding_rates)
        evaluations.append((spec, result, evaluate_portfolio_result(result)))
    windows = portfolio_walk_forward_search(
        normalised,
        specs,
        config,
        funding_rates=funding_rates,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    positive = [window.test_metrics.excess_return > 0 for window in windows]
    positive_fraction = mean(positive) if positive else None
    median_excess = median(window.test_metrics.excess_return for window in windows) if windows else None
    if not windows:
        status = "insufficient_history_for_walk_forward"
    elif positive_fraction is not None and positive_fraction < 0.6:
        status = "not_validated"
    elif median_excess is not None and median_excess <= 0:
        status = "not_validated"
    elif len(windows) < 6:
        status = "low_statistical_power"
    else:
        status = "candidate_requires_forward_test"
    flattened = [bar for series in normalised.values() for bar in series]
    quality = dataset_quality(flattened, interval)
    return {
        "dataset": {
            "market": market,
            "symbols": sorted(normalised),
            "series_count": len(normalised),
            "common_bars": len(timestamps),
            "start": timestamps[0].isoformat(),
            "end": timestamps[-1].isoformat(),
            "interval": interval,
            "quality": quality,
        },
        "method": {
            "signal": "close-only cross-sectional theme proxy; execute target weights at next common bar open",
            "theme_proxy": "relative momentum 45%, short momentum 35%, volume acceleration 20%",
            "regime": "BTC fast/slow trend plus universe breadth",
            "leverage_policy": "perpetuals scale gross exposure from confidence: risk-on 1x to max 5x, risk-off shorts 0.5x to max 2x; volatility reduces confidence",
            "market": market,
            "funding_rates_included": bool(funding_rates),
            "costs": {
                "fee_bps": str(config.fee_bps),
                "slippage_bps": str(config.slippage_bps),
                "spread_bps": str(config.spread_bps),
                "market_impact_bps": str(config.market_impact_bps),
                "one_way_execution_bps": str(config.one_way_execution_bps),
            },
            "walk_forward": {
                "train_days": train_days,
                "test_days": test_days,
                "step_days": step_days,
            },
        },
        "full_sample": [
            {
                "strategy": asdict(spec),
                "metrics": asdict(metrics),
                "trades": _trade_report(result),
            }
            for spec, result, metrics in sorted(evaluations, key=lambda item: item[2].robust_score, reverse=True)
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
            "positive_oos_excess_fraction": positive_fraction,
            "positive_oos_windows": sum(positive),
            "negative_oos_windows": len(positive) - sum(positive),
            "median_oos_excess_return": median_excess,
            "data_quality_status": "complete" if quality["contiguous"] else "gaps_or_duplicates_detected",
            "status": status,
        },
    }
