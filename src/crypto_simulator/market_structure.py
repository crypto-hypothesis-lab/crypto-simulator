"""Point-in-time market-structure event studies.

These labels are observations, not orders.  They deliberately stop before
entry/exit optimization so a weak event can be rejected without manufacturing
a profitable-looking bracket around it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from statistics import mean, median
from typing import Any, Iterable, Mapping

from .derivatives import DerivativesObservation
from .models import OHLCVBar


MARKET_STRUCTURE_STUDY_VERSION = "crypto.market-structure-study.v1"


@dataclass(frozen=True, slots=True)
class MarketStructureEventSpec:
    strategy_id: str
    direction: str
    hypothesis: str
    status: str = "event_study_only"


def default_market_structure_event_specs() -> tuple[MarketStructureEventSpec, ...]:
    return (
        MarketStructureEventSpec(
            "mexc_long_liq_exhaustion_reclaim_v1",
            "long",
            "A price decline with contracting OI and abnormal volume is deleveraging; a later closed-candle reclaim may have positive forward expectancy.",
        ),
        MarketStructureEventSpec(
            "mexc_crowded_long_failure_short_v1",
            "short",
            "Price and OI expansion with crowded positive funding becomes a short candidate only after continuation fails and OI starts to unwind.",
        ),
        MarketStructureEventSpec(
            "mexc_oi_compression_breakout_v1",
            "both",
            "Leverage accumulated during a low-range, neutral-funding compression may support a confirmed volume breakout in its break direction.",
        ),
    )


def _safe_return(current: Decimal, previous: Decimal) -> float | None:
    if previous <= 0:
        return None
    return float(current / previous - Decimal("1"))


def _base_symbol(value: str) -> str:
    normalized = value.upper().replace("/", "-").replace("_", "-")
    if "-" in normalized:
        return normalized.split("-", 1)[0]
    for suffix in ("USDT", "USDC", "USD"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


def _volume_ratio(history: list[OHLCVBar], index: int, lookback: int = 48) -> float | None:
    previous = [float(bar.quote_volume or bar.volume) for bar in history[max(0, index - lookback) : index]]
    current = float(history[index].quote_volume or history[index].volume)
    baseline = median(previous) if previous else 0.0
    return current / baseline if baseline > 0 else None


def _true_range(bar: OHLCVBar, previous_close: Decimal) -> Decimal:
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def _atr(history: list[OHLCVBar], index: int, lookback: int = 14) -> Decimal | None:
    if index < lookback:
        return None
    ranges = [_true_range(history[cursor], history[cursor - 1].close) for cursor in range(index - lookback + 1, index + 1)]
    return sum(ranges, Decimal("0")) / Decimal(len(ranges)) if ranges else None


def _percentile(value: float, history: list[float]) -> float | None:
    if len(history) < 24:
        return None
    return sum(item <= value for item in history) / len(history)


def _forward_outcomes(bars: list[OHLCVBar], index: int, direction: str) -> dict[str, float | None]:
    sign = 1.0 if direction == "long" else -1.0
    close = bars[index].close
    result: dict[str, float | None] = {}
    for hours in (1, 4, 8, 12):
        target = index + hours
        value = _safe_return(bars[target].close, close) if target < len(bars) else None
        result[f"forward_return_{hours}h"] = value * sign if value is not None else None
    future = bars[index + 1 : min(index + 13, len(bars))]
    if not future or close <= 0:
        result["mfe_12h"] = None
        result["mae_12h"] = None
    elif direction == "long":
        result["mfe_12h"] = max(float(bar.high / close - Decimal("1")) for bar in future)
        result["mae_12h"] = max(float(Decimal("1") - bar.low / close) for bar in future)
    else:
        result["mfe_12h"] = max(float(Decimal("1") - bar.low / close) for bar in future)
        result["mae_12h"] = max(float(bar.high / close - Decimal("1")) for bar in future)
    return result


def _event_id(strategy_id: str, symbol: str, timestamp: datetime, direction: str) -> str:
    source = f"{strategy_id}|{symbol}|{timestamp.isoformat()}|{direction}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]


def _observation_price(item: DerivativesObservation) -> Decimal | None:
    return item.index_price or item.mark_price


def _observation_oi(item: DerivativesObservation) -> Decimal | None:
    return item.open_interest_usd or item.open_interest


def _previous_observation(
    history: list[DerivativesObservation],
    *,
    cutoff: datetime,
    as_of: datetime,
) -> DerivativesObservation | None:
    for item in reversed(history):
        if item.observed_at > as_of or item.effective_timestamp > cutoff:
            continue
        if item.effective_timestamp < cutoff - timedelta(hours=3):
            return None
        if item.status in {"fresh", "degraded"} and item.error is None:
            return item
    return None


def _point_in_time_derivatives(
    histories: Mapping[str, list[DerivativesObservation]],
    *,
    as_of: datetime,
    min_venues: int,
) -> dict[str, Any] | None:
    current: list[DerivativesObservation] = []
    for history in histories.values():
        usable = [
            item
            for item in reversed(history)
            if item.observed_at <= as_of
            and item.effective_timestamp <= as_of
            and item.status in {"fresh", "degraded"}
            and item.error is None
        ]
        if usable and as_of - usable[0].observed_at <= timedelta(hours=2):
            current.append(usable[0])
    if len(current) < min_venues:
        return None

    oi_1h: list[float] = []
    oi_4h: list[float] = []
    funding: list[float] = []
    funding_statuses: set[str] = set()
    sources: dict[str, str] = {}
    for item in current:
        sources[item.venue] = item.effective_timestamp.isoformat()
        if item.funding_rate is not None and item.funding_interval_hours and item.funding_interval_hours > 0:
            funding.append(float(item.funding_rate / item.funding_interval_hours))
            funding_statuses.add(item.funding_status)
        current_oi = _observation_oi(item)
        for hours, target in ((1, oi_1h), (4, oi_4h)):
            previous = _previous_observation(
                histories[item.venue],
                cutoff=item.effective_timestamp - timedelta(hours=hours),
                as_of=as_of,
            )
            previous_oi = _observation_oi(previous) if previous is not None else None
            if current_oi is not None and previous_oi is not None and previous_oi > 0:
                target.append(float(current_oi / previous_oi - Decimal("1")))
    if not oi_1h or not oi_4h:
        return None
    return {
        "status": "fresh",
        "fresh_venue_count": len(current),
        "open_interest_change_1h": median(oi_1h),
        "open_interest_change_4h": median(oi_4h),
        "funding_rate_per_hour": median(funding) if funding else None,
        "funding_statuses": sorted(funding_statuses),
        "source_timestamps": sources,
    }


def _summary(events: list[dict[str, Any]], strategy_id: str) -> dict[str, Any]:
    selected = [item for item in events if item["strategy_id"] == strategy_id]
    by_symbol: dict[str, int] = {}
    by_month: dict[str, int] = {}
    for item in selected:
        by_symbol[item["symbol"]] = by_symbol.get(item["symbol"], 0) + 1
        month = item["event_timestamp"][:7]
        by_month[month] = by_month.get(month, 0) + 1
    forward = {}
    for hours in (1, 4, 8, 12):
        values = [item["outcomes"][f"forward_return_{hours}h"] for item in selected]
        values = [float(value) for value in values if value is not None]
        forward[f"mean_{hours}h"] = mean(values) if values else None
        forward[f"median_{hours}h"] = median(values) if values else None
        forward[f"positive_fraction_{hours}h"] = mean(value > 0 for value in values) if values else None
    return {
        "strategy_id": strategy_id,
        "event_count": len(selected),
        "measurement_status": "insufficient_events" if len(selected) < 50 else "measurable",
        "by_symbol": by_symbol,
        "by_month": by_month,
        "forward_outcomes": forward,
    }


def build_market_structure_event_study(
    universe: Mapping[str, list[OHLCVBar]],
    observations: Iterable[DerivativesObservation],
    *,
    min_venues: int = 1,
) -> dict[str, Any]:
    """Label three pre-declared events using only information known at each bar."""

    observations = list(observations)
    specs = default_market_structure_event_specs()
    events: list[dict[str, Any]] = []
    blocked = {"missing_derivatives": 0, "degraded_derivatives": 0, "insufficient_price_history": 0}
    dataset_start: datetime | None = None
    dataset_end: datetime | None = None

    for symbol, raw_bars in sorted(universe.items()):
        bars = sorted(raw_bars, key=lambda item: item.timestamp)
        symbol_observations = sorted(
            (item for item in observations if item.symbol == _base_symbol(symbol)),
            key=lambda item: (max(item.observed_at, item.effective_timestamp), item.venue),
        )
        observation_cursor = 0
        venue_histories: dict[str, list[DerivativesObservation]] = {}
        if bars:
            dataset_start = bars[0].timestamp if dataset_start is None else min(dataset_start, bars[0].timestamp)
            dataset_end = bars[-1].timestamp if dataset_end is None else max(dataset_end, bars[-1].timestamp)
        funding_history: list[float] = []
        for index in range(48, max(48, len(bars) - 1)):
            bar = bars[index]
            if index + 1 >= len(bars):
                break
            while observation_cursor < len(symbol_observations):
                observation = symbol_observations[observation_cursor]
                available_at = max(observation.observed_at, observation.effective_timestamp)
                if available_at > bar.timestamp:
                    break
                venue_histories.setdefault(observation.venue, []).append(observation)
                observation_cursor += 1
            feature = _point_in_time_derivatives(venue_histories, as_of=bar.timestamp, min_venues=min_venues)
            if feature is None:
                blocked["missing_derivatives"] += 1
                continue
            if feature["status"] != "fresh":
                blocked["degraded_derivatives"] += 1
                continue
            if index < 14:
                blocked["insufficient_price_history"] += 1
                continue
            funding = feature["funding_rate_per_hour"]
            funding_usable = bool(feature["funding_statuses"]) and "unknown" not in feature["funding_statuses"]
            funding_rank = _percentile(funding, funding_history) if funding is not None else None
            if funding is not None:
                funding_history.append(funding)
            price_return_6h = _safe_return(bar.close, bars[index - 6].close)
            oi_change_4h = feature["open_interest_change_4h"]
            oi_change_1h = feature["open_interest_change_1h"]
            volume_ratio = _volume_ratio(bars, index)
            atr = _atr(bars, index)
            common = {
                "price_return_6h": price_return_6h,
                "oi_change_1h": oi_change_1h,
                "oi_change_4h": oi_change_4h,
                "funding_rate_per_hour": funding,
                "funding_statuses": feature["funding_statuses"],
                "funding_percentile": funding_rank,
                "volume_ratio_48h_median": volume_ratio,
                "atr": str(atr) if atr is not None else None,
                "derivatives_status": feature["status"],
                "venue_count": feature["fresh_venue_count"],
                "source_timestamps": feature["source_timestamps"],
            }

            candidates: list[tuple[str, str, str]] = []
            reclaim = bar.close > bar.open and bar.close > bars[index - 1].close
            if (
                price_return_6h is not None
                and price_return_6h <= -0.03
                and oi_change_4h is not None
                and oi_change_4h <= -0.03
                and volume_ratio is not None
                and volume_ratio >= 1.5
                and reclaim
            ):
                candidates.append((specs[0].strategy_id, "long", "liquidation_proxy_reclaim"))

            failed_continuation = bar.close < bar.open and bar.close < bars[index - 1].close
            if (
                price_return_6h is not None
                and price_return_6h >= 0.03
                and oi_change_4h is not None
                and oi_change_4h >= 0.03
                and funding_rank is not None
                and funding_rank >= 0.90
                and funding_usable
                and oi_change_1h is not None
                and oi_change_1h < 0
                and failed_continuation
            ):
                candidates.append((specs[1].strategy_id, "short", "crowding_then_oi_unwind"))

            prior = bars[index - 12 : index]
            prior_high = max(item.high for item in prior)
            prior_low = min(item.low for item in prior)
            compressed = bool(atr and atr > 0 and (prior_high - prior_low) / atr <= Decimal("4"))
            neutral_funding = funding_usable and funding_rank is not None and 0.20 <= funding_rank <= 0.80
            confirmed_volume = volume_ratio is not None and volume_ratio >= 1.25
            oi_building = oi_change_4h is not None and oi_change_4h >= 0.015
            if compressed and neutral_funding and confirmed_volume and oi_building:
                if bar.close > prior_high:
                    candidates.append((specs[2].strategy_id, "long", "compression_break_up"))
                elif bar.close < prior_low:
                    candidates.append((specs[2].strategy_id, "short", "compression_break_down"))

            for strategy_id, direction, event_kind in candidates:
                events.append(
                    {
                        "event_id": _event_id(strategy_id, symbol, bar.timestamp, direction),
                        "strategy_id": strategy_id,
                        "strategy_version": strategy_id,
                        "symbol": symbol,
                        "direction": direction,
                        "event_kind": event_kind,
                        "event_timestamp": bar.timestamp.astimezone(timezone.utc).isoformat(),
                        "features": common,
                        "outcomes": _forward_outcomes(bars, index, direction),
                    }
                )

    summaries = [_summary(events, spec.strategy_id) for spec in specs]
    return {
        "schema_version": MARKET_STRUCTURE_STUDY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "event_study_only",
        "paper_eligible": False,
        "live_orders_enabled": False,
        "dataset": {
            "market": "perpetual",
            "symbols": sorted(universe),
            "start": dataset_start.isoformat() if dataset_start else None,
            "end": dataset_end.isoformat() if dataset_end else None,
            "feature_alignment_point_in_time": True,
            "universe_membership_mode": "caller_supplied_snapshot",
            "historical_point_in_time_universe": False,
            "derivatives_observations": len(observations),
        },
        "method": {
            "market": "perpetual",
            "stage": "conditional_forward_return",
            "lookahead_policy": "features use observations with observed_at and effective_timestamp at or before the event candle",
            "liquidation_policy": "liquidation_proxy is explicitly inferred from price, OI, and volume; it is not labelled as an observed liquidation",
            "threshold_policy": "thresholds are frozen v1 research definitions and must not be optimized before event counts and conditional outcomes are reviewed",
        },
        "hypotheses": [asdict(spec) for spec in specs],
        "events": events,
        "full_sample": summaries,
        "blocked_observations": blocked,
        "summary": {
            "status": "event_study_only",
            "event_count": len(events),
            "measurable_strategy_count": sum(item["measurement_status"] == "measurable" for item in summaries),
            "promotion_decision": "hold",
        },
    }
