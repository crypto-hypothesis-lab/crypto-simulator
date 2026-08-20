from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Iterable, Mapping

from .adapters.mexc_contract import MexcContractDetail, MexcTicker
from .models import OHLCVBar
from .timeframes import interval_duration


@dataclass(frozen=True, slots=True)
class LiquidityPolicy:
    """Conservative public-data gate for selecting MEXC perpetual candidates."""

    max_symbols: int = 12
    min_quote_turnover_24h: Decimal = Decimal("10000000")
    min_median_daily_quote_turnover: Decimal = Decimal("5000000")
    max_spread_bps: Decimal = Decimal("25")
    min_history_bars: int = 300
    min_coverage: float = 0.98
    surge_multiple: Decimal = Decimal("3")
    rising_multiple: Decimal = Decimal("1.2")
    surge_size_multiplier: float = 0.5
    rising_size_multiplier: float = 0.75

    def __post_init__(self) -> None:
        if self.max_symbols <= 0 or self.min_history_bars <= 0:
            raise ValueError("max_symbols and min_history_bars must be positive")
        if self.min_quote_turnover_24h <= 0 or self.min_median_daily_quote_turnover <= 0:
            raise ValueError("quote-turnover thresholds must be positive")
        if self.max_spread_bps <= 0 or self.surge_multiple <= 1 or self.rising_multiple <= 1:
            raise ValueError("liquidity thresholds are invalid")
        if not 0 < self.min_coverage <= 1:
            raise ValueError("min_coverage must be in (0, 1]")
        if not 0 < self.surge_size_multiplier <= 1 or not 0 < self.rising_size_multiplier <= 1:
            raise ValueError("volume-regime size multipliers must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class LiquidityAssessment:
    symbol: str
    bars: int
    coverage: float
    latest_24h_quote_turnover: Decimal
    median_daily_quote_turnover_7d: Decimal
    median_daily_quote_turnover_prior_7d: Decimal
    daily_growth_ratio: Decimal
    recent_spike_ratio: Decimal
    volume_regime: str
    spread_bps: Decimal | None
    size_multiplier: float
    passed: bool
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        for key in (
            "latest_24h_quote_turnover",
            "median_daily_quote_turnover_7d",
            "median_daily_quote_turnover_prior_7d",
            "daily_growth_ratio",
            "recent_spike_ratio",
            "spread_bps",
        ):
            if value[key] is not None:
                value[key] = str(value[key])
        value["reasons"] = list(self.reasons)
        return value


def _quote_turnover(bar: OHLCVBar) -> Decimal:
    """Return quote turnover, using close*volume only for legacy CSVs."""

    if bar.quote_volume is not None:
        return max(bar.quote_volume, Decimal("0"))
    return max(bar.close * bar.volume, Decimal("0"))


def _daily_turnovers(bars: list[OHLCVBar], interval: str) -> list[tuple[date, Decimal]]:
    duration = interval_duration(interval)
    expected_per_day = max(int(Decimal("86400") / Decimal(str(duration.total_seconds()))), 1)
    grouped: dict[date, list[OHLCVBar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.timestamp.date()].append(bar)
    complete = [
        (day, sum((_quote_turnover(bar) for bar in sorted(day_bars, key=lambda item: item.timestamp)), Decimal("0")))
        for day, day_bars in sorted(grouped.items())
        if len(day_bars) >= max(int(expected_per_day * 0.95), 1)
    ]
    return complete


def assess_liquidity(
    symbol: str,
    bars: Iterable[OHLCVBar],
    *,
    interval: str = "1hour",
    policy: LiquidityPolicy | None = None,
    spread_bps: Decimal | None = None,
) -> LiquidityAssessment:
    """Assess historical liquidity without looking beyond the supplied bars.

    A recent spike is not rejected automatically. It is accepted only when the
    preceding seven-day base is liquid enough, and it receives a smaller sizing
    multiplier so a one-day volume anomaly cannot create a full-size position.
    """

    policy = policy or LiquidityPolicy()
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    daily = _daily_turnovers(ordered, interval)
    latest_24h_count = max(int(Decimal("86400") / Decimal(str(interval_duration(interval).total_seconds()))), 1)
    latest_24h = sum((_quote_turnover(bar) for bar in ordered[-latest_24h_count:]), Decimal("0"))
    latest_7d = [value for _, value in daily[-7:]]
    prior_7d = [value for _, value in daily[-14:-7]]
    median_7d = Decimal(str(median(latest_7d))) if latest_7d else Decimal("0")
    median_prior = Decimal(str(median(prior_7d))) if prior_7d else Decimal("0")
    daily_growth = median_7d / median_prior if median_prior > 0 else Decimal("0")
    recent_spike = latest_24h / median_7d if median_7d > 0 else Decimal("0")

    if recent_spike >= policy.surge_multiple:
        volume_regime = "surging"
        size_multiplier = policy.surge_size_multiplier
    elif daily_growth >= policy.rising_multiple:
        volume_regime = "rising"
        size_multiplier = policy.rising_size_multiplier
    else:
        volume_regime = "stable"
        size_multiplier = 1.0

    expected_bars = 1
    if len(ordered) >= 2:
        seconds = (ordered[-1].timestamp - ordered[0].timestamp).total_seconds()
        expected_bars = max(int(round(seconds / interval_duration(interval).total_seconds())) + 1, 1)
    coverage = len({bar.timestamp for bar in ordered}) / expected_bars if ordered else 0.0
    reasons: list[str] = []
    if len(ordered) < policy.min_history_bars:
        reasons.append("insufficient_history")
    if coverage < policy.min_coverage:
        reasons.append("coverage_below_floor")
    if latest_24h < policy.min_quote_turnover_24h:
        reasons.append("latest_24h_turnover_below_floor")
    if median_7d < policy.min_median_daily_quote_turnover:
        reasons.append("seven_day_turnover_below_floor")
    if spread_bps is not None and spread_bps > policy.max_spread_bps:
        reasons.append("spread_above_floor")

    return LiquidityAssessment(
        symbol=symbol,
        bars=len(ordered),
        coverage=coverage,
        latest_24h_quote_turnover=latest_24h,
        median_daily_quote_turnover_7d=median_7d,
        median_daily_quote_turnover_prior_7d=median_prior,
        daily_growth_ratio=daily_growth,
        recent_spike_ratio=recent_spike,
        volume_regime=volume_regime,
        spread_bps=spread_bps,
        size_multiplier=size_multiplier,
        passed=not reasons,
        reasons=tuple(reasons),
    )


def select_current_liquid_tickers(
    tickers: Iterable[MexcTicker],
    *,
    policy: LiquidityPolicy | None = None,
    benchmark_symbol: str = "BTC_USDT",
    excluded_symbols: Iterable[str] = (),
    contract_details: Mapping[str, MexcContractDetail] | None = None,
) -> list[MexcTicker]:
    """Select a current snapshot while keeping BTC as the required benchmark."""

    policy = policy or LiquidityPolicy()
    excluded = {symbol.upper() for symbol in excluded_symbols}
    excluded.update({"USDT_USDT", "USDC_USDT", "DAI_USDT"})
    candidates = [
        ticker
        for ticker in tickers
        if ticker.symbol.endswith("_USDT")
        and ticker.symbol.upper() not in excluded
        and (
            contract_details is None
            or (
                ticker.symbol in contract_details
                and contract_details[ticker.symbol].state == 0
                and not contract_details[ticker.symbol].hidden
                and contract_details[ticker.symbol].is_crypto_perpetual
            )
        )
        and ticker.amount_24h >= policy.min_quote_turnover_24h
        and ticker.spread_bps <= policy.max_spread_bps
    ]
    candidates.sort(key=lambda ticker: (ticker.amount_24h, ticker.symbol), reverse=True)
    benchmark = benchmark_symbol.upper()
    if not any(ticker.symbol.upper() == benchmark for ticker in candidates):
        raise ValueError("benchmark did not pass current liquidity filters")
    selected = candidates[: policy.max_symbols]
    if not any(ticker.symbol.upper() == benchmark for ticker in selected):
        benchmark_ticker = next(ticker for ticker in candidates if ticker.symbol.upper() == benchmark)
        selected = [benchmark_ticker] + [ticker for ticker in selected if ticker.symbol.upper() != benchmark]
        selected = selected[: policy.max_symbols]
    return sorted(selected, key=lambda ticker: (ticker.symbol.upper() != benchmark, -ticker.amount_24h, ticker.symbol))


def build_liquidity_manifest(
    tickers: Iterable[MexcTicker],
    *,
    policy: LiquidityPolicy | None = None,
    benchmark_symbol: str = "BTC_USDT",
    assessments: Mapping[str, LiquidityAssessment] | None = None,
) -> dict[str, object]:
    policy = policy or LiquidityPolicy()
    ticker_rows = []
    for ticker in tickers:
        row: dict[str, object] = {
            "symbol": ticker.symbol,
            "amount_24h": str(ticker.amount_24h),
            "volume_24h": str(ticker.volume_24h),
            "last_price": str(ticker.last_price),
            "bid": str(ticker.bid),
            "ask": str(ticker.ask),
            "spread_bps": str(ticker.spread_bps),
            "hold_volume": str(ticker.hold_volume),
            "ticker_timestamp": ticker.timestamp.isoformat(),
        }
        assessment = (assessments or {}).get(ticker.symbol)
        if assessment is not None:
            row["history"] = assessment.to_dict()
        ticker_rows.append(row)
    return {
        "selection_basis": "MEXC public ticker plus point-in-time candle turnover audit",
        "benchmark_symbol": benchmark_symbol,
        "policy": {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(policy).items()
        },
        "symbols": ticker_rows,
        "research_only": True,
        "live_orders_enabled": False,
    }
