from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite, sqrt
from statistics import mean, median, pstdev
from typing import Any, Mapping

from .models import OHLCVBar
from .portfolio import PortfolioConfig, PortfolioTrade
from .research import dataset_quality


def _safe_float(value: float) -> float:
    return value if isfinite(value) else 0.0


def _sma(values: list[Decimal], window: int) -> Decimal:
    if len(values) < window:
        raise ValueError("not enough values for moving average")
    return sum(values[-window:], Decimal("0")) / Decimal(window)


def _true_range(current: OHLCVBar, previous: OHLCVBar) -> Decimal:
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def _atr(series: list[OHLCVBar], end_index: int, window: int) -> Decimal:
    first = max(1, end_index - window + 1)
    values = [_true_range(series[index], series[index - 1]) for index in range(first, end_index + 1)]
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")


def _max_drawdown(curve: list[tuple[str, Decimal]]) -> float:
    peak = 0.0
    result = 0.0
    for _, value in curve:
        equity = float(value)
        peak = max(peak, equity)
        if peak > 0:
            result = max(result, 1.0 - equity / peak)
    return result


def _curve_returns(curve: list[tuple[str, Decimal]]) -> list[float]:
    result: list[float] = []
    for (_, previous), (_, current) in zip(curve, curve[1:]):
        if previous > 0:
            result.append(float(current / previous - Decimal("1")))
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
class SpikeFadeSpec:
    """A short-only post-pump exhaustion hypothesis.

    The signal is formed at a closed bar and executed at the next common bar
    open. A pump is not shorted immediately: the signal requires a subsequent
    rejection of the pump range. The strategy is deliberately separate from
    trend momentum because a continuation breakout is an explicit invalidation
    of this hypothesis.
    """

    name: str
    market: str = "perpetual"
    pump_lookback: int = 6
    pump_return_threshold: float = 0.25
    volume_window: int = 30
    volume_multiple: float = 2.5
    atr_window: int = 20
    pump_atr_multiple: float = 3.0
    confirmation_window: int = 3
    rejection_fraction: float = 0.45
    stop_atr: float = 0.75
    take_profit_r: float = 1.5
    max_holding_bars: int = 12
    cooldown_bars: int = 2
    risk_per_trade: float = 0.003
    max_positions: int = 3
    max_gross_leverage: float = 2.0
    symbol_max_leverage: float = 2.0
    regime_fast: int = 20
    regime_slow: int = 60
    breadth_threshold: float = 0.5
    max_theme_pump_breadth: float = 0.55
    risk_on_threshold_multiplier: float = 1.25

    def __post_init__(self) -> None:
        if self.market not in {"margin", "perpetual"}:
            raise ValueError("spike fade market must be margin or perpetual")
        for name in (
            "pump_lookback",
            "volume_window",
            "atr_window",
            "confirmation_window",
            "max_holding_bars",
            "cooldown_bars",
            "max_positions",
            "regime_fast",
            "regime_slow",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.regime_slow <= self.regime_fast:
            raise ValueError("regime_slow must exceed regime_fast")
        for name in (
            "pump_return_threshold",
            "volume_multiple",
            "pump_atr_multiple",
            "rejection_fraction",
            "stop_atr",
            "take_profit_r",
            "risk_per_trade",
            "max_gross_leverage",
            "symbol_max_leverage",
            "risk_on_threshold_multiplier",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not 0.0 <= self.breadth_threshold <= 1.0:
            raise ValueError("breadth_threshold must be between 0 and 1")
        if not 0.0 <= self.max_theme_pump_breadth <= 1.0:
            raise ValueError("max_theme_pump_breadth must be between 0 and 1")

    @property
    def minimum_history(self) -> int:
        return max(
            self.pump_lookback + self.confirmation_window + 2,
            self.volume_window + self.pump_lookback + 2,
            self.atr_window + 2,
            self.regime_slow + 1,
        )


@dataclass(frozen=True, slots=True)
class SpikeFadeSignal:
    symbol: str
    signal_index: int
    pump_index: int
    pump_timestamp: str
    event_age_bars: int
    pump_return: float
    volume_multiple: float
    pump_atr_multiple: float
    rejection_fraction: float
    regime: str
    breadth: float
    theme_pump_breadth: float
    score: float
    pump_high: Decimal
    atr: Decimal


@dataclass(slots=True)
class SpikeFadeResult:
    initial_cash: Decimal
    final_equity: Decimal
    cash: Decimal
    positions: dict[str, Decimal]
    trades: list[PortfolioTrade]
    round_trips: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    equity_curve: list[tuple[str, Decimal]]
    benchmark_curve: list[tuple[str, Decimal]]
    gross_exposure_curve: list[tuple[str, float]]
    funding_cost: Decimal = Decimal("0")
    financing_cost: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class SpikeFadeMetrics:
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    sortino: float
    max_gross_exposure: float
    turnover: float
    round_trips: int
    execution_trades: int
    win_rate: float
    profit_factor: float | None
    expectancy_per_trade: float
    average_holding_bars: float
    benchmark_return: float
    excess_return: float
    funding_cost_fraction: float
    financing_cost_fraction: float
    robust_score: float


@dataclass(slots=True)
class _LiveShort:
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    entry_timestamp: str
    entry_index: int
    entry_fee: Decimal
    stop_price: Decimal
    target_price: Decimal
    signal: SpikeFadeSignal


def default_spike_fade_specs(market: str) -> list[SpikeFadeSpec]:
    """Return a small, pre-declared grid rather than optimizing thresholds in-sample."""

    if market not in {"margin", "perpetual"}:
        raise ValueError("market must be margin or perpetual")
    max_gross = 2.0
    return [
        SpikeFadeSpec(
            f"{market}_spike_fade_24h_15_reject",
            market=market,
            pump_lookback=6,
            pump_return_threshold=0.15,
            volume_multiple=1.8,
            pump_atr_multiple=2.0,
            confirmation_window=3,
            rejection_fraction=0.30,
            max_holding_bars=12,
            max_gross_leverage=max_gross,
        ),
        SpikeFadeSpec(
            f"{market}_spike_fade_24h_25_loose",
            market=market,
            pump_lookback=6,
            pump_return_threshold=0.25,
            volume_multiple=2.0,
            pump_atr_multiple=2.5,
            confirmation_window=4,
            rejection_fraction=0.35,
            max_holding_bars=12,
            max_gross_leverage=max_gross,
        ),
        SpikeFadeSpec(
            f"{market}_spike_fade_3d_30_reject",
            market=market,
            pump_lookback=18,
            pump_return_threshold=0.30,
            volume_multiple=2.0,
            pump_atr_multiple=3.0,
            confirmation_window=6,
            rejection_fraction=0.35,
            max_holding_bars=18,
            max_gross_leverage=max_gross,
        ),
        SpikeFadeSpec(
            f"{market}_spike_fade_3d_40_reject",
            market=market,
            pump_lookback=18,
            pump_return_threshold=0.40,
            volume_multiple=2.5,
            pump_atr_multiple=4.0,
            confirmation_window=6,
            rejection_fraction=0.45,
            max_holding_bars=18,
            max_gross_leverage=max_gross,
        ),
    ]


def _normalise_universe(universe: Mapping[str, list[OHLCVBar]]) -> tuple[dict[str, list[OHLCVBar]], list[datetime]]:
    normalised = {
        symbol: sorted(series, key=lambda bar: bar.timestamp)
        for symbol, series in universe.items()
        if series
    }
    if not normalised:
        raise ValueError("at least one non-empty asset series is required")
    common = set.intersection(*(set(bar.timestamp for bar in series) for series in normalised.values()))
    timestamps = sorted(common)
    if len(timestamps) < 2:
        raise ValueError("asset series do not share at least two timestamps")
    return normalised, timestamps


def _resolve_benchmark(symbols: list[str], requested: str | None) -> str:
    if requested and requested in symbols:
        return requested
    candidates = [symbol for symbol in symbols if "BTC" in symbol.upper()]
    if candidates:
        return sorted(candidates)[0]
    raise ValueError("a BTC benchmark symbol is required for spike-fade regime filtering")


def _regime_context(
    universe: Mapping[str, list[OHLCVBar]],
    index: int,
    spec: SpikeFadeSpec,
    benchmark_symbol: str,
) -> tuple[str, float, float, bool]:
    benchmark = universe[benchmark_symbol]
    closes = [bar.close for bar in benchmark[: index + 1]]
    fast = _sma(closes, spec.regime_fast)
    slow = _sma(closes, spec.regime_slow)
    usable = [symbol for symbol, series in universe.items() if len(series) > index and symbol != benchmark_symbol]
    breadth_checks = []
    for symbol in usable:
        values = [bar.close for bar in universe[symbol][: index + 1]]
        if len(values) >= spec.regime_fast:
            breadth_checks.append(values[-1] > _sma(values, spec.regime_fast))
    breadth = sum(breadth_checks) / len(breadth_checks) if breadth_checks else 1.0
    risk_on = closes[-1] > slow and fast > slow and breadth >= spec.breadth_threshold
    risk_off = closes[-1] < slow and fast < slow and breadth < spec.breadth_threshold
    return ("risk_on" if risk_on else "risk_off" if risk_off else "neutral", breadth, 1.0 if risk_on else 0.0, risk_on)


def _theme_pump_breadth(
    universe: Mapping[str, list[OHLCVBar]],
    index: int,
    spec: SpikeFadeSpec,
    threshold: float,
) -> float:
    checks = []
    for series in universe.values():
        if len(series) <= index or index < spec.pump_lookback:
            continue
        base = series[index - spec.pump_lookback].close
        checks.append(base > 0 and float(series[index].close / base - Decimal("1")) >= threshold)
    return sum(checks) / len(checks) if checks else 0.0


def _candidate_for_symbol(
    series: list[OHLCVBar],
    index: int,
    spec: SpikeFadeSpec,
    regime: str,
    breadth: float,
    theme_breadth: float,
) -> SpikeFadeSignal | None:
    if index + 1 < spec.minimum_history:
        return None
    threshold_multiplier = spec.risk_on_threshold_multiplier if regime == "risk_on" else 1.0
    return_threshold = spec.pump_return_threshold * threshold_multiplier
    volume_threshold = spec.volume_multiple * (threshold_multiplier if regime == "risk_on" else 1.0)
    atr_threshold = spec.pump_atr_multiple * (threshold_multiplier if regime == "risk_on" else 1.0)
    candidates: list[SpikeFadeSignal] = []
    first_pump = max(spec.pump_lookback + spec.volume_window, index - spec.confirmation_window - 2)
    for pump_index in range(first_pump, index):
        age = index - pump_index
        if age < 1 or age > spec.confirmation_window:
            continue
        base_index = pump_index - spec.pump_lookback
        if base_index < 0:
            continue
        base = series[base_index].close
        if base <= 0:
            continue
        pump_window = series[base_index + 1 : pump_index + 1]
        pump_high = max(bar.high for bar in pump_window)
        pump_return = float(series[pump_index].close / base - Decimal("1"))
        atr = _atr(series, pump_index, spec.atr_window)
        if atr <= 0:
            continue
        pump_atr_multiple = float((pump_high - base) / atr)
        baseline_start = max(0, base_index - spec.volume_window)
        baseline = [bar.volume for bar in series[baseline_start : base_index + 1]]
        baseline_median = median(baseline) if baseline else Decimal("0")
        if baseline_median <= 0:
            continue
        volume_multiple = float(max(bar.volume for bar in pump_window) / baseline_median)
        if pump_return < return_threshold or pump_atr_multiple < atr_threshold or volume_multiple < volume_threshold:
            continue
        if max(bar.high for bar in series[pump_index + 1 : index + 1]) > pump_high:
            continue
        move = pump_high - base
        if move <= 0:
            continue
        rejection = float((pump_high - series[index].close) / move)
        midpoint = base + move * Decimal("0.5")
        broke_previous_low = index > 0 and series[index].close < series[index - 1].low
        if rejection < spec.rejection_fraction or (series[index].close > midpoint and not broke_previous_low):
            continue
        regime_factor = {"risk_off": 1.0, "neutral": 0.75, "risk_on": 0.35}[regime]
        score = _safe_float(
            0.35 * min(pump_return / return_threshold, 3.0)
            + 0.25 * min(volume_multiple / volume_threshold, 3.0)
            + 0.25 * min(rejection / max(spec.rejection_fraction, 0.01), 2.0)
            + 0.15 * regime_factor
            - 0.20 * theme_breadth
        )
        candidates.append(
            SpikeFadeSignal(
                symbol=series[0].symbol,
                signal_index=index,
                pump_index=pump_index,
                pump_timestamp=series[pump_index].timestamp.isoformat(),
                event_age_bars=age,
                pump_return=pump_return,
                volume_multiple=volume_multiple,
                pump_atr_multiple=pump_atr_multiple,
                rejection_fraction=rejection,
                regime=regime,
                breadth=breadth,
                theme_pump_breadth=theme_breadth,
                score=score,
                pump_high=pump_high,
                atr=atr,
            )
        )
    return max(candidates, key=lambda signal: (signal.score, signal.pump_index), default=None)


def _find_signals(
    universe: Mapping[str, list[OHLCVBar]],
    index: int,
    spec: SpikeFadeSpec,
    benchmark_symbol: str,
) -> list[SpikeFadeSignal]:
    if index + 1 < spec.minimum_history:
        return []
    regime, breadth, _, _ = _regime_context(universe, index, spec, benchmark_symbol)
    threshold = spec.pump_return_threshold * (spec.risk_on_threshold_multiplier if regime == "risk_on" else 1.0)
    theme_breadth = _theme_pump_breadth(universe, index, spec, threshold)
    if theme_breadth > spec.max_theme_pump_breadth:
        return []
    signals = []
    for symbol, series in universe.items():
        signal = _candidate_for_symbol(series, index, spec, regime, breadth, theme_breadth)
        if signal:
            signals.append(signal)
    return sorted(signals, key=lambda signal: (signal.score, signal.symbol), reverse=True)[: spec.max_positions]


def _trade_dict(trade: PortfolioTrade) -> dict[str, str]:
    return {
        "timestamp": trade.timestamp,
        "symbol": trade.symbol,
        "side": trade.side,
        "price": str(trade.price),
        "quantity": str(trade.quantity),
        "notional": str(trade.notional),
        "fee": str(trade.fee),
        "reason": trade.reason,
    }


def _signal_dict(signal: SpikeFadeSignal) -> dict[str, Any]:
    value = asdict(signal)
    value["pump_high"] = str(signal.pump_high)
    value["atr"] = str(signal.atr)
    return value


def _close_position(
    *,
    position: _LiveShort,
    raw_price: Decimal,
    timestamp: datetime,
    reason: str,
    cash: Decimal,
    config: PortfolioConfig,
    trades: list[PortfolioTrade],
    round_trips: list[dict[str, Any]],
) -> Decimal:
    price = config.execution_price(raw_price, "buy")
    quantity = abs(position.quantity)
    notional = quantity * price
    fee = notional * config.fee_bps / Decimal("10000")
    cash -= notional + fee
    trades.append(PortfolioTrade(timestamp.isoformat(), position.symbol, "buy", price, quantity, notional, fee, reason))
    pnl = (position.entry_price - price) * quantity - position.entry_fee - fee
    round_trips.append(
        {
            "symbol": position.symbol,
            "entry_timestamp": position.entry_timestamp,
            "exit_timestamp": timestamp.isoformat(),
            "entry_price": str(position.entry_price),
            "exit_price": str(price),
            "quantity": str(quantity),
            "pnl": str(pnl),
            "return_fraction": float(pnl / (position.entry_price * quantity)) if quantity else 0.0,
            "holding_bars": 0,
            "reason": reason,
            "regime": position.signal.regime,
            "score": position.signal.score,
        }
    )
    return cash


def run_spike_fade_backtest(
    universe: Mapping[str, list[OHLCVBar]],
    spec: SpikeFadeSpec,
    config: PortfolioConfig | None = None,
    *,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    start_index: int = 0,
) -> SpikeFadeResult:
    """Run a conservative short-after-pump event backtest.

    Signals use only closed candles through ``t`` and entries use the open of
    ``t+1``. Stop and target checks use OHLC; if both are touched in one bar,
    the stop is assumed to happen first. This deliberately biases the result
    against optimistic fills.
    """

    config = config or PortfolioConfig(max_gross_leverage=Decimal(str(spec.max_gross_leverage)))
    normalised, timestamps = _normalise_universe(universe)
    if start_index < 0 or start_index >= len(timestamps):
        raise ValueError("start_index must refer to a common timestamp")
    benchmark = _resolve_benchmark(sorted(normalised), benchmark_symbol)
    by_timestamp = {symbol: {bar.timestamp: bar for bar in series} for symbol, series in normalised.items()}
    aligned = {
        symbol: [by_timestamp[symbol][timestamp] for timestamp in timestamps]
        for symbol in normalised
    }
    positions: dict[str, _LiveShort] = {}
    last_exit_index: dict[str, int] = {}
    pending: list[SpikeFadeSignal] = []
    trades: list[PortfolioTrade] = []
    round_trips: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    equity_curve: list[tuple[str, Decimal]] = []
    gross_curve: list[tuple[str, float]] = []
    cash = config.initial_cash
    funding_cost = Decimal("0")
    financing_cost = Decimal("0")
    duration_days = max(
        (timestamps[1] - timestamps[0]).total_seconds() / 86_400.0,
        1.0 / 24.0,
    )
    day_fraction = Decimal(str(duration_days))

    for index, timestamp in enumerate(timestamps):
        current = {symbol: by_timestamp[symbol][timestamp] for symbol in normalised}
        if index > start_index:
            for symbol, position in list(positions.items()):
                if position.entry_index >= index:
                    continue
                rate = (funding_rates or {}).get(symbol, {}).get(timestamp, Decimal("0"))
                payment = position.quantity * current[symbol].open * Decimal(str(rate))
                cash -= payment
                funding_cost += payment
                if spec.market == "margin" and config.margin_interest_bps_per_day:
                    interest = (
                        abs(position.quantity * current[symbol].open)
                        * config.margin_interest_bps_per_day
                        / Decimal("10000")
                        * day_fraction
                    )
                    cash -= interest
                    financing_cost += interest

        positions_at_open = set(positions)
        for symbol, position in list(positions.items()):
            bar = current[symbol]
            reason: str | None = None
            raw_exit: Decimal | None = None
            if index > position.entry_index and index - position.entry_index >= spec.max_holding_bars:
                reason, raw_exit = "time_stop", bar.open
            else:
                stop_hit = bar.high >= position.stop_price
                target_hit = bar.low <= position.target_price
                if stop_hit:
                    reason, raw_exit = "stop_loss", position.stop_price
                elif target_hit:
                    reason, raw_exit = "take_profit", position.target_price
            if reason and raw_exit is not None:
                cash = _close_position(
                    position=position,
                    raw_price=raw_exit,
                    timestamp=timestamp,
                    reason=reason,
                    cash=cash,
                    config=config,
                    trades=trades,
                    round_trips=round_trips,
                )
                round_trips[-1]["holding_bars"] = index - position.entry_index
                del positions[symbol]
                last_exit_index[symbol] = index

        equity_at_open = cash + sum(
            position.quantity * current[symbol].open for symbol, position in positions.items()
        )
        existing_gross = sum(
            abs(position.quantity * current[symbol].open) for symbol, position in positions.items()
        )
        max_total_gross = min(
            Decimal(str(spec.max_gross_leverage)),
            config.max_gross_leverage,
        )
        for signal in pending:
            if signal.symbol in positions or signal.symbol in positions_at_open:
                continue
            if index - last_exit_index.get(signal.symbol, -10_000) <= spec.cooldown_bars:
                continue
            if len(positions) >= spec.max_positions or equity_at_open <= 0:
                break
            entry_price = config.execution_price(current[signal.symbol].open, "sell")
            stop_price = signal.pump_high + signal.atr * Decimal(str(spec.stop_atr))
            stop_distance = stop_price - entry_price
            if stop_distance <= 0:
                continue
            risk_budget = equity_at_open * Decimal(str(spec.risk_per_trade))
            quantity = risk_budget / stop_distance
            symbol_cap = Decimal(str(spec.symbol_max_leverage))
            if config.max_leverage_by_symbol and signal.symbol.upper() in config.max_leverage_by_symbol:
                symbol_cap = min(symbol_cap, config.max_leverage_by_symbol[signal.symbol.upper()])
            available_gross = max(equity_at_open * max_total_gross - existing_gross, Decimal("0"))
            available_symbol_gross = equity_at_open * min(max_total_gross, symbol_cap)
            quantity = min(
                quantity,
                available_gross / entry_price if entry_price > 0 else Decimal("0"),
                available_symbol_gross / entry_price if entry_price > 0 else Decimal("0"),
            )
            if quantity <= 0:
                continue
            notional = quantity * entry_price
            fee = notional * config.fee_bps / Decimal("10000")
            cash += notional - fee
            target_price = entry_price - stop_distance * Decimal(str(spec.take_profit_r))
            positions[signal.symbol] = _LiveShort(
                signal.symbol,
                -quantity,
                entry_price,
                timestamp.isoformat(),
                index,
                fee,
                stop_price,
                target_price,
                signal,
            )
            trades.append(PortfolioTrade(timestamp.isoformat(), signal.symbol, "sell", entry_price, quantity, notional, fee, "spike_fade_entry"))
            existing_gross += notional
        pending = []

        equity = cash + sum(position.quantity * current[symbol].close for symbol, position in positions.items())
        gross = (
            sum(abs(position.quantity * current[symbol].close) for symbol, position in positions.items()) / equity
            if equity > 0
            else Decimal("0")
        )
        equity_curve.append((timestamp.isoformat(), equity))
        gross_curve.append((timestamp.isoformat(), _safe_float(float(gross))))

        if index >= start_index and index < len(timestamps) - 1:
            fresh = _find_signals(aligned, index, spec, benchmark)
            pending = [signal for signal in fresh if signal.symbol not in positions]
            signals.extend(_signal_dict(signal) for signal in fresh)

    final_timestamp = timestamps[-1]
    final_bars = {symbol: by_timestamp[symbol][final_timestamp] for symbol in normalised}
    for symbol, position in list(positions.items()):
        cash = _close_position(
            position=position,
            raw_price=final_bars[symbol].close,
            timestamp=final_timestamp,
            reason="end_of_test",
            cash=cash,
            config=config,
            trades=trades,
            round_trips=round_trips,
        )
        round_trips[-1]["holding_bars"] = len(timestamps) - 1 - position.entry_index
        del positions[symbol]
    if equity_curve:
        equity_curve[-1] = (final_timestamp.isoformat(), cash)
        gross_curve[-1] = (final_timestamp.isoformat(), 0.0)
    benchmark_bars = [by_timestamp[benchmark][timestamp] for timestamp in timestamps[start_index:]]
    benchmark_curve: list[tuple[str, Decimal]] = []
    if benchmark_bars:
        entry = config.execution_price(benchmark_bars[0].open, "buy")
        fee_rate = config.fee_bps / Decimal("10000")
        quantity = config.initial_cash / (entry * (Decimal("1") + fee_rate))
        benchmark_cash = config.initial_cash - quantity * entry - quantity * entry * fee_rate
        benchmark_curve = [
            (bar.timestamp.isoformat(), benchmark_cash + quantity * bar.close)
            for bar in benchmark_bars
        ]
    return SpikeFadeResult(
        config.initial_cash,
        cash,
        cash,
        {symbol: Decimal("0") for symbol in normalised},
        trades,
        round_trips,
        signals,
        equity_curve,
        benchmark_curve,
        gross_curve,
        funding_cost,
        financing_cost,
    )


def evaluate_spike_fade_result(result: SpikeFadeResult) -> SpikeFadeMetrics:
    total_return = float(result.final_equity / result.initial_cash - Decimal("1"))
    benchmark_return = (
        float(result.benchmark_curve[-1][1] / result.initial_cash - Decimal("1"))
        if result.benchmark_curve
        else 0.0
    )
    returns = _curve_returns(result.equity_curve)
    exposures = [value for _, value in result.gross_exposure_curve]
    pnls = [float(item["pnl"]) for item in result.round_trips]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    profit_factor = sum(wins) / sum(losses) if losses else (None if wins else 0.0)
    average_holding = mean(float(item["holding_bars"]) for item in result.round_trips) if result.round_trips else 0.0
    return SpikeFadeMetrics(
        total_return=_safe_float(total_return),
        annualized_return=_safe_float(_annualized_return(total_return, result.equity_curve)),
        max_drawdown=_safe_float(_max_drawdown(result.equity_curve)),
        sharpe=_safe_float(_sharpe(returns)),
        sortino=_safe_float(_sortino(returns)),
        max_gross_exposure=_safe_float(max(exposures, default=0.0)),
        turnover=_safe_float(sum(float(trade.notional) for trade in result.trades) / float(result.initial_cash)),
        round_trips=len(result.round_trips),
        execution_trades=len(result.trades),
        win_rate=_safe_float(len(wins) / len(pnls) if pnls else 0.0),
        profit_factor=_safe_float(profit_factor) if profit_factor is not None else None,
        expectancy_per_trade=_safe_float(mean(pnls) / float(result.initial_cash) if pnls else 0.0),
        average_holding_bars=_safe_float(average_holding),
        benchmark_return=_safe_float(benchmark_return),
        excess_return=_safe_float(total_return - benchmark_return),
        funding_cost_fraction=_safe_float(float(result.funding_cost / result.initial_cash)),
        financing_cost_fraction=_safe_float(float(result.financing_cost / result.initial_cash)),
        robust_score=_safe_float(total_return - _max_drawdown(result.equity_curve)),
    )


def _slice_universe(universe: Mapping[str, list[OHLCVBar]], end_timestamp: datetime) -> dict[str, list[OHLCVBar]]:
    return {symbol: [bar for bar in bars if bar.timestamp < end_timestamp] for symbol, bars in universe.items()}


@dataclass(frozen=True, slots=True)
class SpikeFadeWalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_strategy: str
    train_metrics: SpikeFadeMetrics
    test_metrics: SpikeFadeMetrics


def spike_fade_walk_forward_search(
    universe: Mapping[str, list[OHLCVBar]],
    specs: list[SpikeFadeSpec],
    config: PortfolioConfig,
    *,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    train_days: int = 240,
    test_days: int = 60,
    step_days: int = 60,
) -> list[SpikeFadeWalkForwardWindow]:
    _, timestamps = _normalise_universe(universe)
    windows: list[SpikeFadeWalkForwardWindow] = []
    cursor = timestamps[0] + timedelta(days=train_days)
    while cursor + timedelta(days=test_days) <= timestamps[-1]:
        train_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= cursor), len(timestamps))
        test_end = cursor + timedelta(days=test_days)
        test_end_index = next((index for index, timestamp in enumerate(timestamps) if timestamp >= test_end), len(timestamps))
        if train_index <= 0 or test_end_index <= train_index:
            break
        train_universe = _slice_universe(universe, cursor)
        train_evaluations = [
            (
                spec,
                evaluate_spike_fade_result(
                    run_spike_fade_backtest(
                        train_universe,
                        spec,
                        config,
                        funding_rates=funding_rates,
                        benchmark_symbol=benchmark_symbol,
                    )
                ),
            )
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
        test_result = run_spike_fade_backtest(
            test_universe,
            selected_spec,
            config,
            funding_rates=funding_rates,
            benchmark_symbol=benchmark_symbol,
            start_index=train_index,
        )
        windows.append(
            SpikeFadeWalkForwardWindow(
                timestamps[0].isoformat(),
                timestamps[train_index - 1].isoformat(),
                test_timestamps[0].isoformat(),
                test_timestamps[-1].isoformat(),
                selected_spec.name,
                selected_metrics,
                evaluate_spike_fade_result(test_result),
            )
        )
        cursor += timedelta(days=step_days)
    return windows


def spike_fade_research_report(
    universe: Mapping[str, list[OHLCVBar]],
    *,
    market: str,
    specs: list[SpikeFadeSpec] | None = None,
    config: PortfolioConfig | None = None,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    max_leverage_by_symbol: Mapping[str, Decimal] | None = None,
    interval: str = "4hour",
    train_days: int = 240,
    test_days: int = 60,
    step_days: int = 60,
) -> dict[str, Any]:
    if market not in {"margin", "perpetual"}:
        raise ValueError("market must be margin or perpetual")
    specs = specs or default_spike_fade_specs(market)
    if config is None:
        config = PortfolioConfig(
            fee_bps=Decimal("5") if market == "perpetual" else Decimal("10"),
            slippage_bps=Decimal("5"),
            spread_bps=Decimal("10"),
            market_impact_bps=Decimal("5"),
            margin_interest_bps_per_day=Decimal("4") if market == "margin" else Decimal("0"),
            max_gross_leverage=Decimal("2"),
            max_leverage_by_symbol=max_leverage_by_symbol,
        )
    normalised, timestamps = _normalise_universe(universe)
    evaluations = []
    for spec in specs:
        result = run_spike_fade_backtest(
            normalised,
            spec,
            config,
            funding_rates=funding_rates,
            benchmark_symbol=benchmark_symbol,
        )
        evaluations.append((spec, result, evaluate_spike_fade_result(result)))
    windows = spike_fade_walk_forward_search(
        normalised,
        specs,
        config,
        funding_rates=funding_rates,
        benchmark_symbol=benchmark_symbol,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    positive_returns = [window.test_metrics.total_return > 0 for window in windows]
    positive_excess = [window.test_metrics.excess_return > 0 for window in windows]
    median_return = median(window.test_metrics.total_return for window in windows) if windows else None
    median_excess = median(window.test_metrics.excess_return for window in windows) if windows else None
    if not windows:
        status = "insufficient_history_for_walk_forward"
    elif mean(positive_returns) < 0.6 or mean(positive_excess) < 0.6 or (median_return is not None and median_return <= 0) or (median_excess is not None and median_excess <= 0):
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
            "signal": "closed-bar pump and volume/ATR exhaustion with rejection confirmation; execute at next common bar open",
            "risk_controls": "short-only; ATR stop; 1R-scaled target; time stop; stop-first when stop and target share a bar; no averaging down",
            "regime": "BTC fast/slow trend and breadth; risk-on requires stricter pump thresholds",
            "theme_filter": "skip when the current pump breadth exceeds the configured threshold",
            "market": market,
            "funding_rates_included": bool(funding_rates),
            "margin_interest_bps_per_day": str(config.margin_interest_bps_per_day),
            "symbol_leverage_caps": {symbol: str(value) for symbol, value in (config.max_leverage_by_symbol or {}).items()},
            "costs": {
                "fee_bps": str(config.fee_bps),
                "slippage_bps": str(config.slippage_bps),
                "spread_bps": str(config.spread_bps),
                "market_impact_bps": str(config.market_impact_bps),
                "one_way_execution_bps": str(config.one_way_execution_bps),
            },
            "walk_forward": {"train_days": train_days, "test_days": test_days, "step_days": step_days},
        },
        "full_sample": [
            {
                "strategy": asdict(spec),
                "metrics": asdict(metrics),
                "signal_count": len(result.signals),
                "round_trips": result.round_trips,
                "trades": [_trade_dict(trade) for trade in result.trades],
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
            "positive_oos_return_fraction": mean(positive_returns) if positive_returns else None,
            "positive_oos_excess_fraction": mean(positive_excess) if positive_excess else None,
            "positive_oos_windows": sum(positive_returns),
            "negative_oos_windows": len(positive_returns) - sum(positive_returns),
            "median_oos_return": median_return,
            "median_oos_excess_return": median_excess,
            "data_quality_status": "complete" if quality["contiguous"] else "gaps_or_duplicates_detected",
            "status": status,
        },
    }
