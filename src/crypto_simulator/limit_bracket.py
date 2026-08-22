from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
import hashlib
from math import isfinite, sqrt
from statistics import mean, median, pstdev
from typing import Any, Mapping

from .models import OHLCVBar
from .portfolio import PortfolioConfig, PortfolioTrade
from .research import dataset_quality
from .timeframes import interval_duration, resample_ohlcv


_FUNDING_KEYS_CACHE: dict[int, tuple[datetime, ...]] = {}


def _safe_float(value: float) -> float:
    return value if isfinite(value) else 0.0


def _sma(values: list[Decimal], window: int) -> Decimal:
    if len(values) < window:
        raise ValueError("not enough values for moving average")
    return sum(values[-window:], Decimal("0")) / Decimal(window)


def _return(values: list[Decimal], lookback: int) -> float:
    if len(values) <= lookback or values[-1 - lookback] <= 0:
        return 0.0
    return float(values[-1] / values[-1 - lookback] - Decimal("1"))


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


def _percentile_rank(value: float, values: list[float]) -> float:
    if len(values) <= 1:
        return 0.5
    return sum(candidate < value for candidate in values) / (len(values) - 1)


def _realized_volatility(closes: list[Decimal], window: int) -> float | None:
    """Return annualized close-to-close volatility using history through now."""

    if len(closes) < window + 1:
        return None
    returns = [
        float(closes[index] / closes[index - 1] - Decimal("1"))
        for index in range(len(closes) - window, len(closes))
        if closes[index - 1] > 0 and closes[index] > 0
    ]
    if len(returns) < window:
        return None
    return _safe_float(pstdev(returns) * sqrt(365.0))


def _volatility_percentile(closes: list[Decimal], window: int, lookback: int) -> tuple[float | None, float | None]:
    return _volatility_percentile_cached(tuple(closes), window, lookback)


@lru_cache(maxsize=4096)
def _volatility_percentile_cached(
    closes: tuple[Decimal, ...],
    window: int,
    lookback: int,
) -> tuple[float | None, float | None]:
    """Return current realized volatility and its causal trailing percentile."""

    if len(closes) < window + lookback:
        return None, None
    start = max(window, len(closes) - lookback)
    history: list[float] = []
    for end in range(start, len(closes)):
        value = _realized_volatility(closes[: end + 1], window)
        if value is not None:
            history.append(value)
    current = _realized_volatility(closes, window)
    if current is None or len(history) < 2:
        return current, None
    return current, _safe_float(_percentile_rank(current, history))


@dataclass(frozen=True, slots=True)
class LimitBracketSpec:
    """A multi-timeframe pullback strategy with a finite parent/bracket order.

    A signal is only a request to place a resting limit order. The limit is
    never replaced by a market order. When it fills, the simulator attaches a
    stop trigger and a take-profit limit based on the filled price and ATR.
    """

    name: str
    market: str = "perpetual"
    execution_fast: int = 8
    execution_breakout_lookback: int = 12
    atr_window: int = 20
    trend_fast: int = 6
    trend_slow: int = 18
    regime_fast: int = 20
    regime_slow: int = 60
    relative_momentum_window: int = 6
    volume_window: int = 20
    volume_multiple: float = 1.0
    entry_offset_atr: float = 0.35
    stop_atr: float = 1.25
    take_profit_r: float = 2.0
    limit_expiry_bars: int = 8
    max_holding_days: int = 14
    risk_per_trade: float = 0.004
    max_positions: int = 4
    top_n: int = 2
    bottom_n: int = 2
    breadth_threshold: float = 0.5
    min_theme_score: float = 0.55
    risk_off_shorts: bool = True
    max_gross_leverage: float = 5.0
    symbol_max_leverage: float = 5.0
    cancel_on_regime_flip: bool = True
    strategy_family: str = "regime_limit_retest"
    event_only: bool = False
    required_regime: str | None = None
    required_daily_direction: str | None = None
    min_consecutive_green_1h: int | None = None
    max_consecutive_green_1h: int | None = None
    min_prior_consecutive_green_1h: int | None = None
    max_prior_consecutive_green_1h: int | None = None
    min_volume_multiple: float | None = None
    require_rejection_candle: bool = False
    min_rejection_fraction: float | None = None
    relative_return_window: int = 1
    min_relative_return: float | None = None
    min_funding_rate: float | None = None
    require_funding: bool = False
    max_holding_hours: float | None = None
    regime_model: str = "legacy"
    volatility_window: int = 20
    volatility_lookback: int = 120
    volatility_stress_percentile: float = 0.90

    def __post_init__(self) -> None:
        if self.market not in {"spot", "margin", "perpetual"}:
            raise ValueError("market must be spot, margin, or perpetual")
        for name in (
            "execution_fast",
            "execution_breakout_lookback",
            "atr_window",
            "trend_fast",
            "trend_slow",
            "regime_fast",
            "regime_slow",
            "relative_momentum_window",
            "volume_window",
            "limit_expiry_bars",
            "max_holding_days",
            "max_positions",
            "top_n",
            "bottom_n",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.trend_slow <= self.trend_fast:
            raise ValueError("trend_slow must exceed trend_fast")
        if self.regime_slow <= self.regime_fast:
            raise ValueError("regime_slow must exceed regime_fast")
        if self.max_holding_days > 30:
            raise ValueError("max_holding_days must not exceed 30")
        if not 0.0 <= self.breadth_threshold <= 1.0:
            raise ValueError("breadth_threshold must be between 0 and 1")
        if not 0.0 <= self.min_theme_score <= 1.0:
            raise ValueError("min_theme_score must be between 0 and 1")
        for name in (
            "volume_multiple",
            "entry_offset_atr",
            "stop_atr",
            "take_profit_r",
            "risk_per_trade",
            "max_gross_leverage",
            "symbol_max_leverage",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.market == "spot" and self.risk_off_shorts:
            raise ValueError("spot candidates cannot short in risk-off")
        if self.market == "spot" and self.max_gross_leverage > 1:
            raise ValueError("spot candidates cannot use leverage")
        if self.strategy_family.strip() == "":
            raise ValueError("strategy_family must not be empty")
        if self.required_regime is not None and self.required_regime not in {"risk_on", "risk_off"}:
            raise ValueError("required_regime must be risk_on or risk_off")
        if self.required_daily_direction is not None and self.required_daily_direction not in {"RED", "GREEN", "DOJI"}:
            raise ValueError("required_daily_direction must be RED, GREEN, or DOJI")
        for name in ("min_consecutive_green_1h", "max_consecutive_green_1h"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.min_consecutive_green_1h is not None and self.max_consecutive_green_1h is not None:
            if self.max_consecutive_green_1h < self.min_consecutive_green_1h:
                raise ValueError("max_consecutive_green_1h must not be below the minimum")
        for name in ("min_prior_consecutive_green_1h", "max_prior_consecutive_green_1h"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.min_prior_consecutive_green_1h is not None and self.max_prior_consecutive_green_1h is not None:
            if self.max_prior_consecutive_green_1h < self.min_prior_consecutive_green_1h:
                raise ValueError("max_prior_consecutive_green_1h must not be below the minimum")
        if self.min_volume_multiple is not None and self.min_volume_multiple <= 0:
            raise ValueError("min_volume_multiple must be positive")
        if self.min_rejection_fraction is not None and not 0.0 <= self.min_rejection_fraction <= 1.0:
            raise ValueError("min_rejection_fraction must be between 0 and 1")
        if self.relative_return_window <= 0:
            raise ValueError("relative_return_window must be positive")
        if self.max_holding_hours is not None and not 0 < self.max_holding_hours <= 30 * 24:
            raise ValueError("max_holding_hours must be between 0 and 720")
        if self.require_funding and self.min_funding_rate is None:
            raise ValueError("require_funding requires min_funding_rate")
        if self.regime_model not in {"legacy", "router_v2"}:
            raise ValueError("regime_model must be legacy or router_v2")
        if self.volatility_window <= 1:
            raise ValueError("volatility_window must exceed 1")
        if self.volatility_lookback < self.volatility_window:
            raise ValueError("volatility_lookback must not be below volatility_window")
        if not 0.5 <= self.volatility_stress_percentile < 1.0:
            raise ValueError("volatility_stress_percentile must be between 0.5 and 1.0")

    @property
    def minimum_source_history(self) -> int:
        return max(
            self.execution_fast + 1,
            self.execution_breakout_lookback + 1,
            self.atr_window + 1,
            self.volume_window + self.execution_breakout_lookback + 1,
        )


def default_limit_bracket_specs(market: str) -> list[LimitBracketSpec]:
    """Return a small, pre-declared grid instead of a large parameter search."""

    if market not in {"spot", "margin", "perpetual"}:
        raise ValueError("market must be spot, margin, or perpetual")
    if market == "spot":
        gross, symbol_cap, risk_off = 1.0, 1.0, False
    elif market == "margin":
        gross, symbol_cap, risk_off = 2.0, 2.0, True
    else:
        gross, symbol_cap, risk_off = 5.0, 5.0, True
    return [
        LimitBracketSpec(
            f"{market}_limit_retest_fast_0p25_1p25_1p8R_7d",
            market=market,
            entry_offset_atr=0.25,
            stop_atr=1.25,
            take_profit_r=1.8,
            limit_expiry_bars=6,
            max_holding_days=7,
            max_gross_leverage=gross,
            symbol_max_leverage=symbol_cap,
            risk_off_shorts=risk_off,
        ),
        LimitBracketSpec(
            f"{market}_limit_retest_balanced_0p35_1p5_2R_14d",
            market=market,
            entry_offset_atr=0.35,
            stop_atr=1.5,
            take_profit_r=2.0,
            limit_expiry_bars=8,
            max_holding_days=14,
            max_gross_leverage=gross,
            symbol_max_leverage=symbol_cap,
            risk_off_shorts=risk_off,
        ),
        LimitBracketSpec(
            f"{market}_limit_retest_deep_0p55_1p5_2p4R_30d",
            market=market,
            entry_offset_atr=0.55,
            stop_atr=1.5,
            take_profit_r=2.4,
            limit_expiry_bars=12,
            max_holding_days=30,
            max_gross_leverage=gross,
            symbol_max_leverage=symbol_cap,
            risk_off_shorts=risk_off,
        ),
    ]


def default_mexc_event_specs(market: str) -> list[LimitBracketSpec]:
    """Return the small, explicit MEXC event strategy set.

    These are research/Paper candidates only.  The short candidate mirrors the
    external scanner's causal features (daily RED, three-to-four consecutive
    green 1-hour candles, relative strength, and a funding floor).  The long
    candidate reuses the existing regime-aware pullback model with the shorter
    event holding window.  No candidate has live-order authority.
    """

    if market not in {"margin", "perpetual"}:
        raise ValueError("MEXC event candidates require margin or perpetual market")
    return [
        LimitBracketSpec(
            "mexc_event_short_daily_red_green_3_4_v1",
            market=market,
            execution_fast=3,
            execution_breakout_lookback=6,
            atr_window=14,
            trend_fast=6,
            trend_slow=18,
            regime_fast=20,
            regime_slow=60,
            relative_momentum_window=1,
            volume_window=20,
            entry_offset_atr=0.75,
            stop_atr=1.5,
            take_profit_r=2.0,
            limit_expiry_bars=4,
            max_holding_days=1,
            max_holding_hours=8,
            risk_per_trade=0.001,
            max_positions=1,
            top_n=1,
            bottom_n=1,
            breadth_threshold=0.5,
            min_theme_score=0.55,
            risk_off_shorts=True,
            max_gross_leverage=3.0,
            symbol_max_leverage=3.0,
            strategy_family="mexc_event_short",
            event_only=True,
            required_regime="risk_off",
            required_daily_direction="RED",
            min_consecutive_green_1h=3,
            max_consecutive_green_1h=4,
            relative_return_window=1,
            min_relative_return=0.05,
            min_funding_rate=-0.0005,
            require_funding=True,
        ),
        LimitBracketSpec(
            "mexc_event_long_pullback_atr_v1",
            market=market,
            execution_fast=8,
            execution_breakout_lookback=12,
            atr_window=20,
            trend_fast=6,
            trend_slow=18,
            regime_fast=20,
            regime_slow=60,
            relative_momentum_window=6,
            volume_window=20,
            entry_offset_atr=0.35,
            stop_atr=1.5,
            take_profit_r=2.0,
            limit_expiry_bars=4,
            max_holding_days=1,
            max_holding_hours=8,
            risk_per_trade=0.001,
            max_positions=1,
            top_n=1,
            bottom_n=1,
            breadth_threshold=0.5,
            min_theme_score=0.55,
            risk_off_shorts=False,
            max_gross_leverage=3.0,
            symbol_max_leverage=3.0,
            strategy_family="mexc_event_long_pullback",
            required_regime="risk_on",
        ),
        LimitBracketSpec(
            "mexc_event_short_rejection_volume_v1",
            market=market,
            execution_fast=3,
            execution_breakout_lookback=6,
            atr_window=14,
            trend_fast=6,
            trend_slow=18,
            regime_fast=20,
            regime_slow=60,
            relative_momentum_window=1,
            volume_window=20,
            entry_offset_atr=0.50,
            stop_atr=1.35,
            take_profit_r=1.6,
            limit_expiry_bars=4,
            max_holding_days=1,
            max_holding_hours=12,
            risk_per_trade=0.001,
            max_positions=1,
            top_n=1,
            bottom_n=1,
            breadth_threshold=0.5,
            min_theme_score=0.55,
            risk_off_shorts=True,
            max_gross_leverage=5.0,
            symbol_max_leverage=5.0,
            strategy_family="mexc_event_short_rejection_volume",
            event_only=True,
            required_regime="risk_off",
            required_daily_direction="RED",
            min_prior_consecutive_green_1h=3,
            max_prior_consecutive_green_1h=6,
            min_volume_multiple=1.5,
            require_rejection_candle=True,
            min_rejection_fraction=0.55,
            relative_return_window=4,
            min_relative_return=0.03,
            min_funding_rate=-0.0005,
            require_funding=True,
        ),
    ]


def default_mexc_event_v2_specs(market: str) -> list[LimitBracketSpec]:
    """Return event candidates with a volatility-aware permission router.

    v1 remains the frozen comparison/Paper profile.  v2 only changes the
    regime permission layer: it keeps the causal event filters and refuses
    new entries when benchmark realized volatility is in its trailing high
    percentile.  The distinct IDs prevent results from being mixed.
    """

    return [
        replace(
            spec,
            name=spec.name.replace("_v1", "_router_v2"),
            strategy_family=f"{spec.strategy_family}_router_v2",
            regime_model="router_v2",
        )
        for spec in default_mexc_event_specs(market)
    ]


def default_mexc_event_permission_specs(market: str) -> list[LimitBracketSpec]:
    """Return the long-only event entry with regime used only as permission.

    This profile is the direct test of the current research conclusion: a
    risk-on regime permits the event entry, while neutral/risk-off regimes
    simply produce no new entry. It never reverses into a short strategy based
    on the regime, and it keeps a separate strategy ID for clean comparison.
    """

    long_spec = next(
        spec for spec in default_mexc_event_specs(market)
        if spec.name == "mexc_event_long_pullback_atr_v1"
    )
    return [
        replace(
            long_spec,
            name="mexc_event_long_permission_filter_v1",
            strategy_family="mexc_event_long_permission_filter",
            risk_off_shorts=False,
            max_gross_leverage=1.0,
            symbol_max_leverage=1.0,
            required_regime="risk_on",
        )
    ]


@dataclass(frozen=True, slots=True)
class LimitBracketSignal:
    symbol: str
    direction: str
    signal_index: int
    signal_timestamp: str
    limit_price: Decimal
    atr: Decimal
    stop_distance: Decimal
    score: float
    regime: str
    breadth: float
    theme_score: float
    breakout_level: Decimal
    strategy_family: str = "regime_limit_retest"
    daily_direction: str | None = None
    consecutive_green_1h: int | None = None
    prior_consecutive_green_1h: int | None = None
    volume_multiple: float | None = None
    rejection_fraction: float | None = None
    relative_return: float | None = None
    funding_rate: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _MarketContext:
    regime: str
    breadth: float
    state: str = "normal"
    realized_volatility: float | None = None
    volatility_percentile: float | None = None


@dataclass(frozen=True, slots=True)
class _CompletedBars:
    completion_times: tuple[datetime, ...]
    bars: tuple[OHLCVBar, ...]

    def through(self, timestamp: datetime) -> list[OHLCVBar]:
        end = bisect_right(self.completion_times, timestamp)
        return list(self.bars[:end])


@dataclass(frozen=True, slots=True)
class _TimeframeView:
    four_hour: _CompletedBars
    daily: _CompletedBars


@dataclass(slots=True)
class _PendingOrder:
    signal: LimitBracketSignal
    created_index: int


@dataclass(slots=True)
class _LiveBracket:
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    entry_timestamp: str
    entry_index: int
    entry_fee: Decimal
    stop_price: Decimal
    target_price: Decimal
    signal: LimitBracketSignal
    max_favorable_r: float = 0.0
    max_adverse_r: float = 0.0


@dataclass(slots=True)
class LimitBracketResult:
    initial_cash: Decimal
    final_equity: Decimal
    cash: Decimal
    positions: dict[str, Decimal]
    trades: list[PortfolioTrade]
    round_trips: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    order_events: list[dict[str, Any]]
    equity_curve: list[tuple[str, Decimal]]
    benchmark_curve: list[tuple[str, Decimal]]
    gross_exposure_curve: list[tuple[str, float]]
    funding_cost: Decimal = Decimal("0")
    financing_cost: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class LimitBracketMetrics:
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float
    max_gross_exposure: float
    average_gross_exposure: float
    turnover: float
    signals: int
    orders: int
    filled_entries: int
    cancelled_orders: int
    fill_rate: float
    round_trips: int
    win_rate: float
    profit_factor: float | None
    expectancy_per_trade: float
    average_holding_days: float
    stop_losses: int
    take_profits: int
    time_stops: int
    average_mfe_r: float
    average_mae_r: float
    benchmark_return: float
    excess_return: float
    funding_cost_fraction: float
    financing_cost_fraction: float
    robust_score: float


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


def _completed_bars(
    bars: list[OHLCVBar],
    *,
    source_duration: timedelta,
    target_interval: str,
) -> _CompletedBars:
    target_duration = interval_duration(target_interval)
    if target_duration == source_duration:
        resampled = bars
    elif target_duration > source_duration and target_duration.total_seconds() % source_duration.total_seconds() == 0:
        source_interval = next(
            (name for name in ("1min", "5min", "15min", "30min", "1hour", "4hour", "1day") if interval_duration(name) == source_duration),
            None,
        )
        if source_interval is None:
            raise ValueError("source interval is not supported for higher-timeframe resampling")
        resampled = resample_ohlcv(bars, target_interval, source_interval=source_interval)
    else:
        raise ValueError("source interval must not be larger than the requested timeframe")
    completion_times = tuple(bar.timestamp + target_duration - source_duration for bar in resampled)
    return _CompletedBars(completion_times, tuple(resampled))


def _build_timeframe_view(series: list[OHLCVBar], source_interval: str) -> _TimeframeView:
    source_duration = interval_duration(source_interval)
    if source_duration > interval_duration("4hour"):
        raise ValueError("limit-bracket research requires 1-hour or 4-hour source candles")
    return _TimeframeView(
        four_hour=_completed_bars(series, source_duration=source_duration, target_interval="4hour"),
        daily=_completed_bars(series, source_duration=source_duration, target_interval="1day"),
    )


def _resolve_benchmark(symbols: list[str], requested: str | None) -> str:
    if requested and requested in symbols:
        return requested
    candidates = [symbol for symbol in symbols if "BTC" in symbol.upper()]
    if candidates:
        return sorted(candidates)[0]
    raise ValueError("a BTC benchmark symbol is required for regime filtering")


def _context_and_scores(
    aligned: Mapping[str, list[OHLCVBar]],
    views: Mapping[str, _TimeframeView],
    index: int,
    timestamp: datetime,
    spec: LimitBracketSpec,
    benchmark_symbol: str,
    *,
    calculate_scores: bool = True,
) -> tuple[_MarketContext, dict[str, float]]:
    benchmark_daily = views[benchmark_symbol].daily.through(timestamp)
    if len(benchmark_daily) < spec.regime_slow:
        return _MarketContext("warmup", 0.0), {}
    benchmark_daily_closes = [bar.close for bar in benchmark_daily]
    regime_fast = _sma(benchmark_daily_closes, spec.regime_fast)
    regime_slow = _sma(benchmark_daily_closes, spec.regime_slow)
    breadth_checks: list[bool] = []
    for symbol, view in views.items():
        if symbol == benchmark_symbol:
            continue
        daily = view.daily.through(timestamp)
        if len(daily) >= spec.regime_fast:
            closes = [bar.close for bar in daily]
            breadth_checks.append(closes[-1] > _sma(closes, spec.regime_fast))
    breadth = sum(breadth_checks) / len(breadth_checks) if breadth_checks else 1.0
    if benchmark_daily_closes[-1] > regime_slow and regime_fast > regime_slow and breadth >= spec.breadth_threshold:
        regime = "risk_on"
    elif benchmark_daily_closes[-1] < regime_slow and regime_fast < regime_slow and breadth <= 1.0 - spec.breadth_threshold:
        regime = "risk_off"
    else:
        regime = "neutral"

    context = _MarketContext(regime, breadth)
    if spec.regime_model == "router_v2":
        realized_volatility, volatility_percentile = _volatility_percentile(
            benchmark_daily_closes,
            spec.volatility_window,
            spec.volatility_lookback,
        )
        if volatility_percentile is None:
            context = _MarketContext(
                "neutral",
                breadth,
                "insufficient_volatility_history",
                realized_volatility,
                volatility_percentile,
            )
        elif volatility_percentile >= spec.volatility_stress_percentile:
            context = _MarketContext(
                "neutral",
                breadth,
                "stress",
                realized_volatility,
                volatility_percentile,
            )
        else:
            context = _MarketContext(
                regime,
                breadth,
                "normal",
                realized_volatility,
                volatility_percentile,
            )

    if not calculate_scores:
        return context, {}

    benchmark_trend = views[benchmark_symbol].four_hour.through(timestamp)
    if len(benchmark_trend) < max(spec.trend_slow, spec.relative_momentum_window) + 1:
        return context, {}
    benchmark_trend_closes = [bar.close for bar in benchmark_trend]
    benchmark_relative_return = _return(benchmark_trend_closes, spec.relative_momentum_window)
    metrics: dict[str, tuple[float, float, float]] = {}
    for symbol, view in views.items():
        if symbol == benchmark_symbol:
            continue
        trend = view.four_hour.through(timestamp)
        if len(trend) < max(spec.trend_slow, spec.relative_momentum_window) + 1:
            continue
        closes = [bar.close for bar in trend]
        fast_return = _return(closes, spec.relative_momentum_window)
        relative_return = fast_return - benchmark_relative_return
        recent_volume = [bar.volume for bar in trend[-spec.relative_momentum_window:]]
        baseline_end = -spec.relative_momentum_window
        baseline_start = max(0, len(trend) - spec.volume_window - spec.relative_momentum_window)
        baseline = [bar.volume for bar in trend[baseline_start:baseline_end]]
        recent = sum(recent_volume, Decimal("0")) / Decimal(len(recent_volume))
        older = sum(baseline, Decimal("0")) / Decimal(len(baseline)) if baseline else Decimal("0")
        volume_acceleration = float(recent / older - Decimal("1")) if older > 0 else 0.0
        metrics[symbol] = (fast_return, relative_return, volume_acceleration)
    if not metrics:
        return context, {}
    fast_values = [value[0] for value in metrics.values()]
    relative_values = [value[1] for value in metrics.values()]
    volume_values = [value[2] for value in metrics.values()]
    scores = {
        symbol: _safe_float(
            0.30 * _percentile_rank(values[0], fast_values)
            + 0.50 * _percentile_rank(values[1], relative_values)
            + 0.20 * _percentile_rank(values[2], volume_values)
        )
        for symbol, values in metrics.items()
    }
    return context, scores


def _daily_direction(bars: list[OHLCVBar]) -> str | None:
    if not bars:
        return None
    latest = bars[-1]
    if latest.close > latest.open:
        return "GREEN"
    if latest.close < latest.open:
        return "RED"
    return "DOJI"


def _consecutive_green_1h(series: list[OHLCVBar], index: int) -> int:
    count = 0
    for bar in reversed(series[: index + 1]):
        if bar.close <= bar.open:
            break
        count += 1
    return count


def _latest_funding_rate(
    rates: Mapping[datetime, Decimal] | None,
    timestamp: datetime,
) -> Decimal | None:
    if not rates:
        return None
    cache_key = id(rates)
    keys = _FUNDING_KEYS_CACHE.get(cache_key)
    if keys is None or len(keys) != len(rates):
        keys = tuple(sorted(rates))
        _FUNDING_KEYS_CACHE[cache_key] = keys
    position = bisect_right(keys, timestamp) - 1
    return rates[keys[position]] if position >= 0 else None


def _volume_multiple(series: list[OHLCVBar], index: int, window: int) -> float | None:
    if index < window:
        return None
    baseline = [bar.volume for bar in series[index - window:index]]
    baseline_volume = median(baseline) if baseline else Decimal("0")
    if baseline_volume <= 0:
        return None
    return float(series[index].volume / baseline_volume)


def _rejection_fraction(bar: OHLCVBar) -> float:
    candle_range = bar.high - bar.low
    if candle_range <= 0:
        return 0.0
    return float((bar.high - bar.close) / candle_range)


def _event_features(
    aligned: Mapping[str, list[OHLCVBar]],
    views: Mapping[str, _TimeframeView],
    symbol: str,
    benchmark_symbol: str,
    index: int,
    timestamp: datetime,
    spec: LimitBracketSpec,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None,
) -> dict[str, Any]:
    series = aligned[symbol]
    benchmark = aligned[benchmark_symbol]
    window = spec.relative_return_window
    relative_return: float | None = None
    if index >= window and benchmark[index - window].close > 0 and series[index - window].close > 0:
        asset_return = series[index].close / series[index - window].close - Decimal("1")
        benchmark_return = benchmark[index].close / benchmark[index - window].close - Decimal("1")
        relative_return = float(asset_return - benchmark_return)
    funding_rate = _latest_funding_rate((funding_rates or {}).get(symbol), timestamp)
    current = series[index]
    return {
        "daily_direction": _daily_direction(views[symbol].daily.through(timestamp)),
        "consecutive_green_1h": _consecutive_green_1h(series, index),
        "prior_consecutive_green_1h": _consecutive_green_1h(series, index - 1) if index > 0 else 0,
        "volume_multiple": _volume_multiple(series, index, spec.volume_window),
        "is_red_candle": current.close < current.open,
        "rejection_fraction": _rejection_fraction(current),
        "relative_return": relative_return,
        "funding_rate": funding_rate,
    }


def _event_filter_passes(
    context: _MarketContext,
    spec: LimitBracketSpec,
    features: Mapping[str, Any],
) -> bool:
    if spec.required_regime is not None and context.regime != spec.required_regime:
        return False
    if spec.required_daily_direction is not None and features.get("daily_direction") != spec.required_daily_direction:
        return False
    green_count = features.get("consecutive_green_1h")
    if spec.min_consecutive_green_1h is not None and (green_count is None or green_count < spec.min_consecutive_green_1h):
        return False
    if spec.max_consecutive_green_1h is not None and (green_count is None or green_count > spec.max_consecutive_green_1h):
        return False
    prior_green_count = features.get("prior_consecutive_green_1h")
    if spec.min_prior_consecutive_green_1h is not None and (
        prior_green_count is None or prior_green_count < spec.min_prior_consecutive_green_1h
    ):
        return False
    if spec.max_prior_consecutive_green_1h is not None and (
        prior_green_count is None or prior_green_count > spec.max_prior_consecutive_green_1h
    ):
        return False
    volume_multiple = features.get("volume_multiple")
    if spec.min_volume_multiple is not None and (
        volume_multiple is None or volume_multiple < spec.min_volume_multiple
    ):
        return False
    if spec.require_rejection_candle and not features.get("is_red_candle", False):
        return False
    rejection_fraction = features.get("rejection_fraction")
    if spec.min_rejection_fraction is not None and (
        rejection_fraction is None or rejection_fraction < spec.min_rejection_fraction
    ):
        return False
    relative_return = features.get("relative_return")
    if spec.min_relative_return is not None and (relative_return is None or relative_return < spec.min_relative_return):
        return False
    funding_rate = features.get("funding_rate")
    if spec.require_funding and funding_rate is None:
        return False
    if spec.min_funding_rate is not None and (
        funding_rate is None or funding_rate < Decimal(str(spec.min_funding_rate))
    ):
        return False
    return True


def _event_candidate_for_symbol(
    series: list[OHLCVBar],
    index: int,
    timestamp: datetime,
    spec: LimitBracketSpec,
    context: _MarketContext,
    features: Mapping[str, Any],
    direction: str,
) -> LimitBracketSignal | None:
    if index + 1 < spec.minimum_source_history:
        return None
    atr = _atr(series, index, spec.atr_window)
    if atr <= 0:
        return None
    current = series[index]
    limit_price = (
        current.close - atr * Decimal(str(spec.entry_offset_atr))
        if direction == "long"
        else current.close + atr * Decimal(str(spec.entry_offset_atr))
    )
    relative_return = features.get("relative_return")
    relative_score = 0.5
    if spec.min_relative_return and relative_return is not None:
        relative_score = min(max(relative_return / spec.min_relative_return, 0.0), 2.0) / 2.0
    green_count = int(
        features.get("prior_consecutive_green_1h")
        if spec.min_prior_consecutive_green_1h is not None
        else features.get("consecutive_green_1h")
        or 0
    )
    green_ceiling = spec.max_prior_consecutive_green_1h or spec.max_consecutive_green_1h or 4
    green_score = min(green_count / max(green_ceiling, 1), 1.0)
    regime_score = 1.0 if context.regime == spec.required_regime else 0.5
    volume_multiple = float(features.get("volume_multiple") or 0.0)
    rejection_fraction = float(features.get("rejection_fraction") or 0.0)
    if spec.min_volume_multiple is not None or spec.require_rejection_candle:
        volume_score = min(volume_multiple / max(spec.min_volume_multiple or 1.0, 1.0), 2.0) / 2.0
        score = max(
            0.0,
            min(
                1.0,
                0.30 * relative_score
                + 0.20 * green_score
                + 0.20 * volume_score
                + 0.15 * rejection_fraction
                + 0.15 * regime_score,
            ),
        )
    else:
        score = max(0.0, min(1.0, 0.45 * relative_score + 0.35 * green_score + 0.20 * regime_score))
    return LimitBracketSignal(
        series[0].symbol,
        direction,
        index,
        timestamp.isoformat(),
        limit_price,
        atr,
        atr * Decimal(str(spec.stop_atr)),
        score,
        context.regime,
        context.breadth,
        1.0 if direction == "short" else 0.0,
        current.close,
        spec.strategy_family,
        features.get("daily_direction"),
        features.get("consecutive_green_1h"),
        features.get("prior_consecutive_green_1h"),
        features.get("volume_multiple"),
        features.get("rejection_fraction"),
        relative_return,
        features.get("funding_rate"),
    )


def _candidate_for_symbol(
    series: list[OHLCVBar],
    index: int,
    timestamp: datetime,
    spec: LimitBracketSpec,
    context: _MarketContext,
    theme_score: float,
    direction: str,
) -> LimitBracketSignal | None:
    if index + 1 < spec.minimum_source_history:
        return None
    current = series[index]
    prior = series[index - spec.execution_breakout_lookback:index]
    if len(prior) < spec.execution_breakout_lookback:
        return None
    atr = _atr(series, index, spec.atr_window)
    if atr <= 0:
        return None
    closes = [bar.close for bar in series[: index + 1]]
    fast = _sma(closes, spec.execution_fast)
    baseline_start = max(0, index - spec.volume_window - spec.execution_breakout_lookback)
    baseline = [bar.volume for bar in series[baseline_start:index]]
    baseline_volume = median(baseline) if baseline else Decimal("0")
    if baseline_volume <= 0 or current.volume < baseline_volume * Decimal(str(spec.volume_multiple)):
        return None
    breakout_high = max(bar.high for bar in prior)
    breakout_low = min(bar.low for bar in prior)
    if direction == "long":
        if current.close <= breakout_high or current.close <= fast:
            return None
        breakout_level = breakout_high
        limit_price = current.close - atr * Decimal(str(spec.entry_offset_atr))
        if limit_price < breakout_level - atr:
            return None
        extension = float((current.close - breakout_level) / atr)
    else:
        if current.close >= breakout_low or current.close >= fast:
            return None
        breakout_level = breakout_low
        limit_price = current.close + atr * Decimal(str(spec.entry_offset_atr))
        if limit_price > breakout_level + atr:
            return None
        extension = float((breakout_level - current.close) / atr)
    trend_strength = min(abs(extension), 1.0)
    score = _safe_float(0.55 * (theme_score if direction == "long" else 1.0 - theme_score) + 0.45 * trend_strength)
    return LimitBracketSignal(
        series[0].symbol,
        direction,
        index,
        timestamp.isoformat(),
        limit_price,
        atr,
        atr * Decimal(str(spec.stop_atr)),
        score,
        context.regime,
        context.breadth,
        theme_score,
        breakout_level,
    )


def _find_signals(
    aligned: Mapping[str, list[OHLCVBar]],
    views: Mapping[str, _TimeframeView],
    index: int,
    timestamps: list[datetime],
    spec: LimitBracketSpec,
    benchmark_symbol: str,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
) -> list[LimitBracketSignal]:
    timestamp = timestamps[index]
    context, scores = _context_and_scores(
        aligned,
        views,
        index,
        timestamp,
        spec,
        benchmark_symbol,
        calculate_scores=not spec.event_only,
    )
    if context.regime not in {"risk_on", "risk_off"}:
        return []
    if spec.required_regime is not None and context.regime != spec.required_regime:
        return []

    if spec.event_only:
        signals: list[LimitBracketSignal] = []
        for symbol, series in aligned.items():
            if symbol == benchmark_symbol:
                continue
            features = _event_features(
                aligned,
                views,
                symbol,
                benchmark_symbol,
                index,
                timestamp,
                spec,
                funding_rates,
            )
            if not _event_filter_passes(context, spec, features):
                continue
            signal = _event_candidate_for_symbol(series, index, timestamp, spec, context, features, "short")
            if signal is not None:
                signals.append(signal)
        return sorted(signals, key=lambda signal: (signal.score, signal.symbol), reverse=True)[: spec.max_positions]

    if not scores:
        return []
    ranked_long = set(sorted(scores, key=lambda symbol: (scores[symbol], symbol), reverse=True)[: spec.top_n])
    ranked_short = set(sorted(scores, key=lambda symbol: (scores[symbol], symbol))[: spec.bottom_n])
    signals: list[LimitBracketSignal] = []
    for symbol, series in aligned.items():
        if symbol == benchmark_symbol or symbol not in scores:
            continue
        trend = views[symbol].four_hour.through(timestamp)
        if len(trend) < spec.trend_slow:
            continue
        trend_closes = [bar.close for bar in trend]
        trend_fast = _sma(trend_closes, spec.trend_fast)
        trend_slow = _sma(trend_closes, spec.trend_slow)
        trend_up = trend[-1].close > trend_slow and trend_fast > trend_slow
        trend_down = trend[-1].close < trend_slow and trend_fast < trend_slow
        features = _event_features(
            aligned,
            views,
            symbol,
            benchmark_symbol,
            index,
            timestamp,
            spec,
            funding_rates,
        )
        if not _event_filter_passes(context, spec, features):
            continue
        if context.regime == "risk_on" and symbol in ranked_long and scores[symbol] >= spec.min_theme_score and trend_up:
            signal = _candidate_for_symbol(series, index, timestamp, spec, context, scores[symbol], "long")
            if signal:
                signals.append(
                    replace(
                        signal,
                        strategy_family=spec.strategy_family,
                        daily_direction=features.get("daily_direction"),
                        consecutive_green_1h=features.get("consecutive_green_1h"),
                        relative_return=features.get("relative_return"),
                        funding_rate=features.get("funding_rate"),
                    )
                )
        elif (
            context.regime == "risk_off"
            and spec.risk_off_shorts
            and spec.market != "spot"
            and symbol in ranked_short
            and 1.0 - scores[symbol] >= spec.min_theme_score
            and trend_down
        ):
            signal = _candidate_for_symbol(series, index, timestamp, spec, context, scores[symbol], "short")
            if signal:
                signals.append(
                    replace(
                        signal,
                        strategy_family=spec.strategy_family,
                        daily_direction=features.get("daily_direction"),
                        consecutive_green_1h=features.get("consecutive_green_1h"),
                        relative_return=features.get("relative_return"),
                        funding_rate=features.get("funding_rate"),
                    )
                )
    return sorted(signals, key=lambda signal: (signal.score, signal.symbol), reverse=True)[: spec.max_positions]


def _limit_fill(bar: OHLCVBar, direction: str, limit_price: Decimal) -> Decimal | None:
    if direction == "long":
        if bar.open <= limit_price:
            return bar.open
        if bar.low <= limit_price:
            return limit_price
    else:
        if bar.open >= limit_price:
            return bar.open
        if bar.high >= limit_price:
            return limit_price
    return None


def _take_profit_fill(bar: OHLCVBar, direction: str, target_price: Decimal) -> Decimal:
    if direction == "long":
        return bar.open if bar.open >= target_price else target_price
    return bar.open if bar.open <= target_price else target_price


def _entry_quantity(
    *,
    signal: LimitBracketSignal,
    equity: Decimal,
    existing_gross: Decimal,
    bar: OHLCVBar,
    spec: LimitBracketSpec,
    config: PortfolioConfig,
) -> Decimal:
    if equity <= 0 or signal.stop_distance <= 0 or signal.limit_price <= 0:
        return Decimal("0")
    confidence_scale = Decimal(str(0.5 + 0.5 * max(0.0, min(1.0, signal.score))))
    risk_budget = equity * Decimal(str(spec.risk_per_trade)) * confidence_scale
    quantity = risk_budget / signal.stop_distance
    max_total_gross = min(Decimal(str(spec.max_gross_leverage)), config.max_gross_leverage)
    symbol_cap = Decimal(str(spec.symbol_max_leverage))
    if config.max_leverage_by_symbol and signal.symbol.upper() in config.max_leverage_by_symbol:
        symbol_cap = min(symbol_cap, config.max_leverage_by_symbol[signal.symbol.upper()])
    available_gross = max(equity * max_total_gross - existing_gross, Decimal("0"))
    available_symbol_gross = equity * min(max_total_gross, symbol_cap)
    return min(
        quantity,
        available_gross / signal.limit_price if signal.limit_price > 0 else Decimal("0"),
        available_symbol_gross / signal.limit_price if signal.limit_price > 0 else Decimal("0"),
    )


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


def _signal_dict(signal: LimitBracketSignal) -> dict[str, Any]:
    value = asdict(signal)
    for key in ("limit_price", "atr", "stop_distance", "breakout_level"):
        value[key] = str(getattr(signal, key))
    if signal.funding_rate is not None:
        value["funding_rate"] = str(signal.funding_rate)
    return value


def build_limit_bracket_signal_event(
    universe: Mapping[str, list[OHLCVBar]],
    spec: LimitBracketSpec,
    *,
    interval: str = "1hour",
    config: PortfolioConfig | None = None,
    benchmark_symbol: str | None = None,
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
) -> dict[str, Any]:
    """Build one dashboard/notification-ready decision snapshot.

    This is deliberately read-only. It does not submit an order and it does
    not require credentials. The private operations repository can ingest the
    stable ``schema_version`` and ``idempotency_key`` fields, then decide
    whether to paper-execute, notify Discord, or publish the snapshot to a
    member dashboard.
    """

    if config is None:
        config = PortfolioConfig(
            max_gross_leverage=Decimal(str(spec.max_gross_leverage)),
            max_leverage_by_symbol=None,
        )
    normalised, timestamps = _normalise_universe(universe)
    benchmark = _resolve_benchmark(sorted(normalised), benchmark_symbol)
    by_timestamp = {symbol: {bar.timestamp: bar for bar in series} for symbol, series in normalised.items()}
    aligned = {symbol: [by_timestamp[symbol][timestamp] for timestamp in timestamps] for symbol in normalised}
    views = {symbol: _build_timeframe_view(series, interval) for symbol, series in aligned.items()}
    index = len(timestamps) - 1
    timestamp = timestamps[index]
    context, scores = _context_and_scores(aligned, views, index, timestamp, spec, benchmark)
    fresh = _find_signals(aligned, views, index, timestamps, spec, benchmark, funding_rates)
    ranking = [
        {
            "rank": rank,
            "symbol": symbol,
            "score": score,
            "eligible_long": context.regime == "risk_on" and score >= spec.min_theme_score,
            "eligible_short": context.regime == "risk_off" and spec.risk_off_shorts and spec.market != "spot" and 1.0 - score >= spec.min_theme_score,
        }
        for rank, (symbol, score) in enumerate(
            sorted(scores.items(), key=lambda item: (item[1], item[0]), reverse=True),
            start=1,
        )
    ]
    candidates: list[dict[str, Any]] = []
    for signal in fresh:
        if signal.direction == "long":
            stop_price = signal.limit_price - signal.stop_distance
            target_price = signal.limit_price + signal.stop_distance * Decimal(str(spec.take_profit_r))
            entry_side = "buy"
        else:
            stop_price = signal.limit_price + signal.stop_distance
            target_price = signal.limit_price - signal.stop_distance * Decimal(str(spec.take_profit_r))
            entry_side = "sell"
        identity = "|".join(
            (
                spec.name,
                signal.symbol,
                signal.direction,
                timestamp.isoformat(),
                str(signal.limit_price),
            )
        )
        signal_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        candidates.append(
            {
                "signal_id": signal_id,
                "idempotency_key": f"limit-bracket:{signal_id}",
                "symbol": signal.symbol,
                "direction": signal.direction,
                "confidence": signal.score,
                "theme_score": signal.theme_score,
                "order": {
                    "side": entry_side,
                    "type": "limit",
                    "price": str(signal.limit_price),
                    "reduce_only": False,
                    "price_is_planned_limit": True,
                },
                "stop_loss": {
                    "type": "stop_market_protective",
                    "trigger_price": str(stop_price),
                    "reduce_only": True,
                },
                "take_profit": {
                    "type": "limit",
                    "trigger_price": str(target_price),
                    "price": str(target_price),
                    "reduce_only": True,
                },
                "expires_at": (timestamp + interval_duration(interval) * spec.limit_expiry_bars).isoformat(),
                "max_holding_days": spec.max_holding_days,
                **(
                    {"max_holding_hours": spec.max_holding_hours}
                    if spec.max_holding_hours is not None
                    else {}
                ),
                "risk": {
                    "risk_per_trade": spec.risk_per_trade,
                    "sizing": "risk_based_after_fill",
                    "max_gross_leverage": min(spec.max_gross_leverage, float(config.max_gross_leverage)),
                    "symbol_leverage_cap": float(
                        min(
                            Decimal(str(spec.symbol_max_leverage)),
                            (config.max_leverage_by_symbol or {}).get(signal.symbol.upper(), Decimal(str(spec.symbol_max_leverage))),
                        )
                    ),
                    "requires_current_equity_and_positions": True,
                },
                "evidence": {
                    "regime": signal.regime,
                    "breadth": signal.breadth,
                    "breakout_level": str(signal.breakout_level),
                    "atr": str(signal.atr),
                    "stop_distance": str(signal.stop_distance),
                    "strategy_family": signal.strategy_family,
                    "daily_direction": signal.daily_direction,
                    "consecutive_green_1h": signal.consecutive_green_1h,
                    "prior_consecutive_green_1h": signal.prior_consecutive_green_1h,
                    "volume_multiple": signal.volume_multiple,
                    "rejection_fraction": signal.rejection_fraction,
                    "relative_return": signal.relative_return,
                    "funding_rate": str(signal.funding_rate) if signal.funding_rate is not None else None,
                },
            }
        )
    if context.state == "insufficient_volatility_history":
        no_trade_reason = "insufficient_volatility_history"
    elif context.state == "stress":
        no_trade_reason = "volatility_stress"
    elif context.regime == "warmup":
        no_trade_reason = "insufficient_higher_timeframe_history"
    elif context.regime == "neutral":
        no_trade_reason = "neutral_regime"
    elif context.regime == "risk_off" and spec.market == "spot":
        no_trade_reason = "risk_off_spot_no_new_long"
    elif not candidates:
        no_trade_reason = "no_valid_limit_retest_signal"
    else:
        no_trade_reason = None
    decision = "actionable" if candidates else "no_trade"
    action_types = sorted({candidate["direction"] for candidate in candidates})
    recommended_action = action_types[0] if len(action_types) == 1 else "mixed" if action_types else "wait"
    breadth_confidence = (
        context.breadth if context.regime == "risk_on" else 1.0 - context.breadth if context.regime == "risk_off" else 0.0
    )
    return {
        "schema_version": "crypto.bracket-signal.v1",
        "signal_source": "crypto-simulator",
        "strategy_id": spec.name,
        "strategy_version": spec.name,
        "event_type": "crypto_investment_decision",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": f"decision:{spec.name}:{timestamp.isoformat()}:{spec.market}",
        "source": {
            "exchange": sorted({bar.exchange for series in aligned.values() for bar in series}),
            "market": spec.market,
            "interval": interval,
            "closed_bar_timestamp": timestamp.isoformat(),
            "benchmark_symbol": benchmark,
            "symbols": sorted(aligned),
        },
        "decision": decision,
        "recommended_action": recommended_action,
        "regime": {
            "label": context.regime,
            "state": context.state,
            "breadth": context.breadth,
            "confidence": breadth_confidence,
            "realized_volatility": context.realized_volatility,
            "volatility_percentile": context.volatility_percentile,
            "new_long_allowed": context.regime == "risk_on",
            "new_short_allowed": context.regime == "risk_off" and spec.risk_off_shorts and spec.market != "spot",
        },
        "ranking": ranking,
        "candidates": candidates,
        "no_trade_reason": no_trade_reason,
        "guardrails": {
            "market_order_fallback": False,
            "cancel_on_regime_flip": spec.cancel_on_regime_flip,
            "limit_expiry_bars": spec.limit_expiry_bars,
            "max_holding_days": spec.max_holding_days,
            **(
                {"max_holding_hours": spec.max_holding_hours}
                if spec.max_holding_hours is not None
                else {}
            ),
            "stop_target_same_bar_policy": "stop_first",
            "strategy_status": "research_candidate_only",
        },
        "strategy": {
            **asdict(spec),
            "funding_data_available": bool(funding_rates),
        },
    }


def _close_position(
    *,
    position: _LiveBracket,
    bar: OHLCVBar,
    timestamp: datetime,
    reason: str,
    raw_price: Decimal,
    config: PortfolioConfig,
    trades: list[PortfolioTrade],
    round_trips: list[dict[str, Any]],
) -> Decimal:
    exit_side = "sell" if position.direction == "long" else "buy"
    if reason == "take_profit":
        price = _take_profit_fill(bar, position.direction, raw_price)
        fee_bps = config.fee_for("maker")
    elif reason == "stop_loss":
        # A stop is modeled as a protective, marketable close. Intrabar stops
        # pay the configured adverse execution cost; gaps fill at the open.
        gap_through = (position.direction == "long" and bar.open <= raw_price) or (
            position.direction == "short" and bar.open >= raw_price
        )
        price = (
            config.execution_price(bar.open, exit_side, additional_bps=config.stop_gap_penalty_bps)
            if gap_through
            else config.execution_price(raw_price, exit_side)
        )
        fee_bps = config.fee_for("taker")
    else:
        price = config.execution_price(raw_price, exit_side)
        fee_bps = config.fee_for("taker")
    quantity = abs(position.quantity)
    notional = quantity * price
    fee = notional * fee_bps / Decimal("10000")
    if position.direction == "long":
        cash_delta = notional - fee
    else:
        cash_delta = -(notional + fee)
    trades.append(PortfolioTrade(timestamp.isoformat(), position.symbol, exit_side, price, quantity, notional, fee, reason))
    pnl = (price - position.entry_price) * quantity if position.direction == "long" else (position.entry_price - price) * quantity
    pnl -= position.entry_fee + fee
    holding_days = (timestamp - datetime.fromisoformat(position.entry_timestamp)).total_seconds() / 86_400.0
    round_trips.append(
        {
            "symbol": position.symbol,
            "direction": position.direction,
            "entry_timestamp": position.entry_timestamp,
            "exit_timestamp": timestamp.isoformat(),
            "entry_price": str(position.entry_price),
            "exit_price": str(price),
            "quantity": str(quantity),
            "pnl": str(pnl),
            "return_fraction": float(pnl / (position.entry_price * quantity)) if quantity else 0.0,
            "holding_days": holding_days,
            "reason": reason,
            "regime": position.signal.regime,
            "score": position.signal.score,
            "mfe_r": position.max_favorable_r,
            "mae_r": position.max_adverse_r,
        }
    )
    return cash_delta


def _update_excursion(position: _LiveBracket, bar: OHLCVBar) -> None:
    """Track favorable/adverse movement in initial-risk units.

    MAE/MFE are diagnostic only. They never change exits or sizing, so adding
    this telemetry cannot alter the existing Paper/backtest behavior.
    """

    risk_distance = abs(position.entry_price - position.stop_price)
    if risk_distance <= 0:
        return
    if position.direction == "long":
        favorable = (bar.high - position.entry_price) / risk_distance
        adverse = (position.entry_price - bar.low) / risk_distance
    else:
        favorable = (position.entry_price - bar.low) / risk_distance
        adverse = (bar.high - position.entry_price) / risk_distance
    position.max_favorable_r = max(position.max_favorable_r, max(float(favorable), 0.0))
    position.max_adverse_r = max(position.max_adverse_r, max(float(adverse), 0.0))


def _benchmark_curve(
    bars: list[OHLCVBar],
    start_index: int,
    config: PortfolioConfig,
) -> list[tuple[str, Decimal]]:
    if start_index >= len(bars):
        return []
    fee_rate = config.fee_for("taker") / Decimal("10000")
    entry = config.execution_price(bars[start_index].open, "buy")
    quantity = config.initial_cash / (entry * (Decimal("1") + fee_rate))
    cash = config.initial_cash - quantity * entry - quantity * entry * fee_rate
    return [(bar.timestamp.isoformat(), cash + quantity * bar.close) for bar in bars[start_index:]]


def run_limit_bracket_backtest(
    universe: Mapping[str, list[OHLCVBar]],
    spec: LimitBracketSpec,
    config: PortfolioConfig | None = None,
    *,
    interval: str = "1hour",
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    start_index: int = 0,
) -> LimitBracketResult:
    """Backtest resting-limit entries and attached protective exits.

    Signals use only closed source candles through ``t``. An order created at
    ``t`` can fill from ``t+1`` through its expiry window. Limit orders fill at
    the limit, or at a better opening gap price. Existing positions are checked
    before new fills; if both stop and target are touched in one candle, the
    stop is assumed first.
    """

    config = config or PortfolioConfig(
        fee_bps=Decimal("5") if spec.market == "perpetual" else Decimal("10"),
        slippage_bps=Decimal("5"),
        spread_bps=Decimal("10"),
        market_impact_bps=Decimal("5"),
        margin_interest_bps_per_day=Decimal("4") if spec.market == "margin" else Decimal("0"),
        max_gross_leverage=Decimal(str(spec.max_gross_leverage)),
    )
    normalised, timestamps = _normalise_universe(universe)
    if start_index < 0 or start_index >= len(timestamps):
        raise ValueError("start_index must refer to a common timestamp")
    benchmark = _resolve_benchmark(sorted(normalised), benchmark_symbol)
    by_timestamp = {symbol: {bar.timestamp: bar for bar in series} for symbol, series in normalised.items()}
    aligned = {symbol: [by_timestamp[symbol][timestamp] for timestamp in timestamps] for symbol in normalised}
    views = {symbol: _build_timeframe_view(series, interval) for symbol, series in aligned.items()}
    positions: dict[str, _LiveBracket] = {}
    pending: dict[str, _PendingOrder] = {}
    last_exit_index: dict[str, int] = {}
    trades: list[PortfolioTrade] = []
    round_trips: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    order_events: list[dict[str, Any]] = []
    equity_curve: list[tuple[str, Decimal]] = []
    gross_curve: list[tuple[str, float]] = []
    cash = config.initial_cash
    funding_cost = Decimal("0")
    financing_cost = Decimal("0")

    for index, timestamp in enumerate(timestamps):
        current = {symbol: aligned[symbol][index] for symbol in aligned}
        if index > start_index:
            day_fraction = Decimal(str(max((timestamps[index] - timestamps[index - 1]).total_seconds() / 86_400.0, 0.0)))
            for symbol, position in list(positions.items()):
                rate = (funding_rates or {}).get(symbol, {}).get(timestamp, Decimal("0"))
                payment = position.quantity * current[symbol].open * Decimal(str(rate))
                cash -= payment
                funding_cost += payment
                if spec.market == "margin" and config.margin_interest_bps_per_day:
                    interest = abs(position.quantity * current[symbol].open) * config.margin_interest_bps_per_day / Decimal("10000") * day_fraction
                    cash -= interest
                    financing_cost += interest

        # 1. Manage existing brackets before considering new entry fills.
        for symbol, position in list(positions.items()):
            bar = current[symbol]
            _update_excursion(position, bar)
            holding_days = (timestamp - datetime.fromisoformat(position.entry_timestamp)).total_seconds() / 86_400.0
            reason: str | None = None
            raw_exit: Decimal | None = None
            max_holding_hours = spec.max_holding_hours if spec.max_holding_hours is not None else spec.max_holding_days * 24
            if holding_days * 24 >= max_holding_hours:
                reason, raw_exit = "time_stop", bar.open
            else:
                stop_hit = bar.low <= position.stop_price if position.direction == "long" else bar.high >= position.stop_price
                target_hit = bar.high >= position.target_price if position.direction == "long" else bar.low <= position.target_price
                if stop_hit:
                    reason, raw_exit = "stop_loss", position.stop_price
                elif target_hit:
                    reason, raw_exit = "take_profit", position.target_price
            if reason and raw_exit is not None:
                cash += _close_position(
                    position=position,
                    bar=bar,
                    timestamp=timestamp,
                    reason=reason,
                    raw_price=raw_exit,
                    config=config,
                    trades=trades,
                    round_trips=round_trips,
                )
                del positions[symbol]
                last_exit_index[symbol] = index

        context, _ = _context_and_scores(aligned, views, index, timestamp, spec, benchmark)
        for symbol, order in list(pending.items()):
            age = index - order.created_index
            if age <= 0:
                continue
            if age > spec.limit_expiry_bars:
                order_events.append({"timestamp": timestamp.isoformat(), "symbol": symbol, "event": "cancel", "reason": "limit_expired"})
                del pending[symbol]
                continue
            invalidated = spec.cancel_on_regime_flip and (
                (order.signal.direction == "long" and context.regime != "risk_on")
                or (order.signal.direction == "short" and context.regime != "risk_off")
            )
            if invalidated:
                order_events.append({"timestamp": timestamp.isoformat(), "symbol": symbol, "event": "cancel", "reason": "regime_invalidated"})
                del pending[symbol]
                continue
            if symbol in positions or len(positions) >= spec.max_positions:
                continue
            fill_price = _limit_fill(current[symbol], order.signal.direction, order.signal.limit_price)
            if fill_price is None:
                continue
            adverse_rate = config.adverse_selection_bps / Decimal("10000")
            if order.signal.direction == "long":
                fill_price *= Decimal("1") + adverse_rate
            else:
                fill_price *= Decimal("1") - adverse_rate
            equity_at_open = cash + sum(position.quantity * current[name].open for name, position in positions.items())
            existing_gross = sum(abs(position.quantity * current[name].open) for name, position in positions.items())
            quantity = _entry_quantity(
                signal=order.signal,
                equity=equity_at_open,
                existing_gross=existing_gross,
                bar=current[symbol],
                spec=spec,
                config=config,
            )
            if quantity <= 0:
                continue
            notional = quantity * fill_price
            fee = notional * config.fee_for("maker") / Decimal("10000")
            if order.signal.direction == "long":
                cash -= notional + fee
            else:
                cash += notional - fee
            stop_distance = order.signal.stop_distance
            if order.signal.direction == "long":
                stop_price = fill_price - stop_distance
                target_price = fill_price + stop_distance * Decimal(str(spec.take_profit_r))
                side = "buy"
            else:
                stop_price = fill_price + stop_distance
                target_price = fill_price - stop_distance * Decimal(str(spec.take_profit_r))
                side = "sell"
            signed_quantity = quantity if order.signal.direction == "long" else -quantity
            positions[symbol] = _LiveBracket(
                symbol,
                order.signal.direction,
                signed_quantity,
                fill_price,
                timestamp.isoformat(),
                index,
                fee,
                stop_price,
                target_price,
                order.signal,
            )
            trades.append(PortfolioTrade(timestamp.isoformat(), symbol, side, fill_price, quantity, notional, fee, "limit_entry"))
            order_events.append(
                {
                    "timestamp": timestamp.isoformat(),
                    "symbol": symbol,
                    "event": "fill",
                    "reason": "limit_entry",
                    "direction": order.signal.direction,
                    "limit_price": str(order.signal.limit_price),
                    "fill_price": str(fill_price),
                    "stop_price": str(stop_price),
                    "target_price": str(target_price),
                }
            )
            del pending[symbol]

        equity = cash + sum(position.quantity * current[symbol].close for symbol, position in positions.items())
        gross = sum(abs(position.quantity * current[symbol].close) for symbol, position in positions.items()) / equity if equity > 0 else Decimal("0")
        equity_curve.append((timestamp.isoformat(), equity))
        gross_curve.append((timestamp.isoformat(), _safe_float(float(gross))))

        if index >= start_index:
            fresh = _find_signals(aligned, views, index, timestamps, spec, benchmark, funding_rates)
            for signal in fresh:
                if signal.symbol in positions or signal.symbol in pending:
                    continue
                if index - last_exit_index.get(signal.symbol, -10_000) <= 1:
                    continue
                pending[signal.symbol] = _PendingOrder(signal, index)
                signals.append(_signal_dict(signal))
                order_events.append(
                    {
                        "timestamp": timestamp.isoformat(),
                        "symbol": signal.symbol,
                        "event": "placed",
                        "direction": signal.direction,
                        "limit_price": str(signal.limit_price),
                        "expires_after_bars": spec.limit_expiry_bars,
                    }
                )

    final_timestamp = timestamps[-1]
    final_bars = {symbol: aligned[symbol][-1] for symbol in aligned}
    for symbol, position in list(positions.items()):
        cash += _close_position(
            position=position,
            bar=final_bars[symbol],
            timestamp=final_timestamp,
            reason="end_of_test",
            raw_price=final_bars[symbol].close,
            config=config,
            trades=trades,
            round_trips=round_trips,
        )
        del positions[symbol]
    if equity_curve:
        equity_curve[-1] = (final_timestamp.isoformat(), cash)
        gross_curve[-1] = (final_timestamp.isoformat(), 0.0)
    benchmark_curve = _benchmark_curve(aligned[benchmark], start_index, config)
    return LimitBracketResult(
        config.initial_cash,
        cash,
        cash,
        {symbol: Decimal("0") for symbol in aligned},
        trades,
        round_trips,
        signals,
        order_events,
        equity_curve,
        benchmark_curve,
        gross_curve,
        funding_cost,
        financing_cost,
    )


def evaluate_limit_bracket_result(result: LimitBracketResult) -> LimitBracketMetrics:
    total_return = float(result.final_equity / result.initial_cash - Decimal("1"))
    benchmark_return = float(result.benchmark_curve[-1][1] / result.initial_cash - Decimal("1")) if result.benchmark_curve else 0.0
    exposures = [value for _, value in result.gross_exposure_curve]
    placements = sum(event.get("event") == "placed" for event in result.order_events)
    fills = sum(event.get("event") == "fill" for event in result.order_events)
    cancels = sum(event.get("event") == "cancel" for event in result.order_events)
    pnls = [float(item["pnl"]) for item in result.round_trips]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [-pnl for pnl in pnls if pnl < 0]
    profit_factor = sum(wins) / sum(losses) if losses else (None if wins else 0.0)
    average_holding = mean(float(item["holding_days"]) for item in result.round_trips) if result.round_trips else 0.0
    average_mfe = mean(float(item.get("mfe_r", 0.0)) for item in result.round_trips) if result.round_trips else 0.0
    average_mae = mean(float(item.get("mae_r", 0.0)) for item in result.round_trips) if result.round_trips else 0.0
    total_fees = sum(float(trade.notional) for trade in result.trades)
    return LimitBracketMetrics(
        total_return=_safe_float(total_return),
        annualized_return=_safe_float(_annualized_return(total_return, result.equity_curve)),
        max_drawdown=_safe_float(_max_drawdown(result.equity_curve)),
        sharpe=_safe_float(_sharpe(_curve_returns(result.equity_curve))),
        max_gross_exposure=_safe_float(max(exposures, default=0.0)),
        average_gross_exposure=_safe_float(mean(exposures) if exposures else 0.0),
        turnover=_safe_float(total_fees / float(result.initial_cash)),
        signals=len(result.signals),
        orders=placements,
        filled_entries=fills,
        cancelled_orders=cancels,
        fill_rate=_safe_float(fills / placements if placements else 0.0),
        round_trips=len(result.round_trips),
        win_rate=_safe_float(len(wins) / len(pnls) if pnls else 0.0),
        profit_factor=_safe_float(profit_factor) if profit_factor is not None else None,
        expectancy_per_trade=_safe_float(mean(pnls) / float(result.initial_cash) if pnls else 0.0),
        average_holding_days=_safe_float(average_holding),
        stop_losses=sum(item["reason"] == "stop_loss" for item in result.round_trips),
        take_profits=sum(item["reason"] == "take_profit" for item in result.round_trips),
        time_stops=sum(item["reason"] == "time_stop" for item in result.round_trips),
        average_mfe_r=_safe_float(average_mfe),
        average_mae_r=_safe_float(average_mae),
        benchmark_return=_safe_float(benchmark_return),
        excess_return=_safe_float(total_return - benchmark_return),
        funding_cost_fraction=_safe_float(float(result.funding_cost / result.initial_cash)),
        financing_cost_fraction=_safe_float(float(result.financing_cost / result.initial_cash)),
        robust_score=_safe_float(total_return - _max_drawdown(result.equity_curve)),
    )


def _slice_universe(universe: Mapping[str, list[OHLCVBar]], end_timestamp: datetime) -> dict[str, list[OHLCVBar]]:
    return {symbol: [bar for bar in bars if bar.timestamp < end_timestamp] for symbol, bars in universe.items()}


@dataclass(frozen=True, slots=True)
class LimitBracketWalkForwardWindow:
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_strategy: str
    selection_status: str
    train_metrics: LimitBracketMetrics
    test_metrics: LimitBracketMetrics


def _qualifies_training_candidate(metrics: LimitBracketMetrics, minimum_trades: int) -> bool:
    """Reject flattering zero/small-sample candidates before OOS selection."""

    return (
        metrics.round_trips >= minimum_trades
        and metrics.expectancy_per_trade > 0
        and metrics.robust_score > 0
        and metrics.profit_factor is not None
        and metrics.profit_factor >= 1.15
    )


def _no_trade_metrics(template: LimitBracketMetrics) -> LimitBracketMetrics:
    return replace(
        template,
        total_return=0.0,
        annualized_return=0.0,
        max_drawdown=0.0,
        sharpe=0.0,
        max_gross_exposure=0.0,
        average_gross_exposure=0.0,
        turnover=0.0,
        signals=0,
        orders=0,
        filled_entries=0,
        cancelled_orders=0,
        fill_rate=0.0,
        round_trips=0,
        win_rate=0.0,
        profit_factor=None,
        expectancy_per_trade=0.0,
        average_holding_days=0.0,
        stop_losses=0,
        take_profits=0,
        time_stops=0,
        average_mfe_r=0.0,
        average_mae_r=0.0,
        benchmark_return=0.0,
        excess_return=0.0,
        funding_cost_fraction=0.0,
        financing_cost_fraction=0.0,
        robust_score=0.0,
    )


def limit_bracket_walk_forward_search(
    universe: Mapping[str, list[OHLCVBar]],
    specs: list[LimitBracketSpec],
    config: PortfolioConfig,
    *,
    interval: str = "1hour",
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
    minimum_training_trades: int = 20,
) -> list[LimitBracketWalkForwardWindow]:
    if train_days <= 0 or test_days <= 0 or step_days <= 0:
        raise ValueError("walk-forward periods must be positive")
    _, timestamps = _normalise_universe(universe)
    windows: list[LimitBracketWalkForwardWindow] = []
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
                evaluate_limit_bracket_result(
                    run_limit_bracket_backtest(
                        train_universe,
                        spec,
                        config,
                        interval=interval,
                        funding_rates=funding_rates,
                        benchmark_symbol=benchmark_symbol,
                    )
                ),
            )
            for spec in specs
        ]
        qualified = [item for item in train_evaluations if _qualifies_training_candidate(item[1], minimum_training_trades)]
        ranking_pool = qualified or train_evaluations
        selected_spec, selected_metrics = max(
            ranking_pool,
            key=lambda item: (item[1].robust_score, item[1].excess_return, -item[1].max_drawdown),
        )
        selection_status = "selected" if qualified else "no_qualified_training_candidate"
        test_timestamps = timestamps[train_index:test_end_index]
        if not qualified:
            windows.append(
                LimitBracketWalkForwardWindow(
                    timestamps[0].isoformat(),
                    timestamps[train_index - 1].isoformat(),
                    test_timestamps[0].isoformat(),
                    test_timestamps[-1].isoformat(),
                    "NO_STRATEGY",
                    selection_status,
                    selected_metrics,
                    _no_trade_metrics(selected_metrics),
                )
            )
            cursor += timedelta(days=step_days)
            continue
        test_universe = {symbol: [bar for bar in bars if bar.timestamp <= test_timestamps[-1]] for symbol, bars in universe.items()}
        test_result = run_limit_bracket_backtest(
            test_universe,
            selected_spec,
            config,
            interval=interval,
            funding_rates=funding_rates,
            benchmark_symbol=benchmark_symbol,
            start_index=train_index,
        )
        windows.append(
            LimitBracketWalkForwardWindow(
                timestamps[0].isoformat(),
                timestamps[train_index - 1].isoformat(),
                test_timestamps[0].isoformat(),
                test_timestamps[-1].isoformat(),
                selected_spec.name,
                selection_status,
                selected_metrics,
                evaluate_limit_bracket_result(test_result),
            )
        )
        cursor += timedelta(days=step_days)
    return windows


def limit_bracket_research_report(
    universe: Mapping[str, list[OHLCVBar]],
    *,
    market: str,
    specs: list[LimitBracketSpec] | None = None,
    config: PortfolioConfig | None = None,
    interval: str = "1hour",
    funding_rates: Mapping[str, Mapping[datetime, Decimal]] | None = None,
    benchmark_symbol: str | None = None,
    max_leverage_by_symbol: Mapping[str, Decimal] | None = None,
    train_days: int = 180,
    test_days: int = 60,
    step_days: int = 60,
    minimum_training_trades: int = 20,
) -> dict[str, Any]:
    if market not in {"spot", "margin", "perpetual"}:
        raise ValueError("market must be spot, margin, or perpetual")
    specs = specs or default_limit_bracket_specs(market)
    if config is None:
        config = PortfolioConfig(
            fee_bps=Decimal("5") if market == "perpetual" else Decimal("10"),
            slippage_bps=Decimal("5"),
            spread_bps=Decimal("10"),
            market_impact_bps=Decimal("5"),
            margin_interest_bps_per_day=Decimal("4") if market == "margin" else Decimal("0"),
            max_gross_leverage=Decimal("1") if market == "spot" else Decimal("2") if market == "margin" else Decimal("5"),
            max_leverage_by_symbol=max_leverage_by_symbol,
        )
    normalised, timestamps = _normalise_universe(universe)
    evaluations = []
    for spec in specs:
        result = run_limit_bracket_backtest(
            normalised,
            spec,
            config,
            interval=interval,
            funding_rates=funding_rates,
            benchmark_symbol=benchmark_symbol,
        )
        evaluations.append((spec, result, evaluate_limit_bracket_result(result)))
    windows = limit_bracket_walk_forward_search(
        normalised,
        specs,
        config,
        interval=interval,
        funding_rates=funding_rates,
        benchmark_symbol=benchmark_symbol,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        minimum_training_trades=minimum_training_trades,
    )
    traded_windows = [window for window in windows if window.selection_status == "selected" and window.test_metrics.round_trips > 0]
    positive_returns = [window.test_metrics.total_return > 0 for window in traded_windows]
    positive_excess = [window.test_metrics.excess_return > 0 for window in traded_windows]
    median_return = median(window.test_metrics.total_return for window in traded_windows) if traded_windows else None
    median_excess = median(window.test_metrics.excess_return for window in traded_windows) if traded_windows else None
    if not windows:
        status = "insufficient_history_for_walk_forward"
    elif not traded_windows:
        status = "no_qualified_oos_trades"
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
            "layers": {
                "1d_regime": "benchmark fast/slow trend plus universe breadth",
                "4h_theme": "cross-sectional relative momentum, trend alignment, and volume acceleration",
                f"{interval}_execution": "closed-bar breakout confirmation followed by a resting pullback/retest limit order",
            },
            "entry": "long limit = signal close - ATR * entry_offset; short limit = signal close + ATR * entry_offset; no market fallback; better opening gaps are filled at open",
            "bracket": "filled entry attaches an ATR stop trigger and R-multiple take-profit limit; stop is checked first when both are touched",
            "order_lifetime": (
                "unfilled parent limit expires after the configured bars; "
                f"max holding period is capped at {max(spec.max_holding_days for spec in specs)} days "
                f"and {max((spec.max_holding_hours or spec.max_holding_days * 24) for spec in specs):g} hours "
                "per strategy, never exceeding 30 days"
            ),
            "market": market,
            "funding_rates_included": bool(funding_rates),
            "margin_interest_bps_per_day": str(config.margin_interest_bps_per_day),
            "symbol_leverage_caps": {symbol: str(value) for symbol, value in (config.max_leverage_by_symbol or {}).items()},
            "costs": {
                "fee_bps": str(config.fee_bps),
                "maker_fee_bps": str(config.fee_for("maker")),
                "taker_fee_bps": str(config.fee_for("taker")),
                "slippage_bps": str(config.slippage_bps),
                "spread_bps": str(config.spread_bps),
                "market_impact_bps": str(config.market_impact_bps),
                "adverse_selection_bps": str(config.adverse_selection_bps),
                "stop_gap_penalty_bps": str(config.stop_gap_penalty_bps),
                "one_way_execution_bps": str(config.one_way_execution_bps),
                "stress_multipliers": [1.0, 1.5, 2.0],
            },
            "walk_forward": {
                "train_days": train_days,
                "test_days": test_days,
                "step_days": step_days,
                "minimum_training_trades": minimum_training_trades,
                "selection_profit_factor_floor": 1.15,
                "no_qualified_candidate_policy": "NO_STRATEGY / NO_TRADE",
            },
        },
        "full_sample": [
            {
                "strategy": asdict(spec),
                "metrics": asdict(metrics),
                "round_trips": result.round_trips,
                "signals": result.signals,
                "orders": result.order_events,
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
                "selection_status": window.selection_status,
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
            "negative_oos_windows": len(traded_windows) - sum(positive_returns),
            "flat_or_no_trade_oos_windows": len(windows) - len(traded_windows),
            "no_qualified_strategy_windows": sum(window.selection_status != "selected" for window in windows),
            "median_oos_return": median_return,
            "median_oos_excess_return": median_excess,
            "data_quality_status": "complete" if quality["contiguous"] else "gaps_or_duplicates_detected",
            "status": status,
        },
    }
