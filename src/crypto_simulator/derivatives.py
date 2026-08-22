"""Exchange-neutral derivatives regime features.

This module is deliberately Shadow-only.  It produces observations and
diagnostics that can be compared with the canonical strategy, but it never
changes a signal or creates an order instruction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from statistics import median
from typing import Any, Iterable


DERIVATIVES_FEATURE_VERSION = "derivatives-feature.v1"
DERIVATIVES_REGIME_VERSION = "derivatives-regime.v1"
DATA_STALE = "DATA_STALE"


def _decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _utc(value)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return _utc(datetime.fromisoformat(normalized))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _median_decimal(values: Iterable[Decimal]) -> Decimal | None:
    values = list(values)
    return Decimal(str(median(values))) if values else None


@dataclass(frozen=True, slots=True)
class DerivativesObservation:
    """One public derivatives snapshot normalized to UTC and USD semantics."""

    venue: str
    symbol: str
    market_type: str
    observed_at: datetime
    exchange_timestamp: datetime | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    open_interest: Decimal | None = None
    open_interest_usd: Decimal | None = None
    funding_rate: Decimal | None = None
    funding_interval_hours: Decimal | None = None
    volume_24h_usd: Decimal | None = None
    instrument: str | None = None
    status: str = "fresh"
    source: str = ""
    source_version: str = "public-api.v1"
    missing_fields: tuple[str, ...] = ()
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        if self.exchange_timestamp is not None:
            object.__setattr__(self, "exchange_timestamp", _utc(self.exchange_timestamp))
        object.__setattr__(self, "symbol", self.symbol.upper())
        if self.instrument is not None:
            object.__setattr__(self, "instrument", self.instrument.upper())
        for name in (
            "mark_price",
            "index_price",
            "open_interest",
            "open_interest_usd",
            "funding_rate",
            "funding_interval_hours",
            "volume_24h_usd",
        ):
            object.__setattr__(self, name, _decimal(getattr(self, name)))
        object.__setattr__(self, "missing_fields", tuple(sorted(set(self.missing_fields))))

    @property
    def effective_timestamp(self) -> datetime:
        return self.exchange_timestamp or self.observed_at

    @property
    def age_seconds(self) -> float:
        return max((datetime.now(timezone.utc) - self.observed_at).total_seconds(), 0.0)

    @classmethod
    def error_observation(
        cls,
        venue: str,
        symbol: str,
        *,
        observed_at: datetime,
        error: str,
    ) -> "DerivativesObservation":
        return cls(
            venue=venue,
            symbol=symbol,
            market_type="perpetual",
            observed_at=observed_at,
            status="error",
            source="public-api",
            missing_fields=("mark_price", "open_interest", "funding_rate"),
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["observed_at"] = _iso(self.observed_at)
        data["exchange_timestamp"] = _iso(self.exchange_timestamp)
        for name in (
            "mark_price",
            "index_price",
            "open_interest",
            "open_interest_usd",
            "funding_rate",
            "funding_interval_hours",
            "volume_24h_usd",
        ):
            if data[name] is not None:
                data[name] = str(data[name])
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DerivativesObservation":
        return cls(
            venue=str(payload["venue"]),
            symbol=str(payload["symbol"]),
            market_type=str(payload.get("market_type", "perpetual")),
            observed_at=_datetime(payload["observed_at"]) or datetime.now(timezone.utc),
            exchange_timestamp=_datetime(payload.get("exchange_timestamp")),
            mark_price=payload.get("mark_price"),
            index_price=payload.get("index_price"),
            open_interest=payload.get("open_interest"),
            open_interest_usd=payload.get("open_interest_usd"),
            funding_rate=payload.get("funding_rate"),
            funding_interval_hours=payload.get("funding_interval_hours"),
            volume_24h_usd=payload.get("volume_24h_usd"),
            instrument=payload.get("instrument"),
            status=str(payload.get("status", "fresh")),
            source=str(payload.get("source", "")),
            source_version=str(payload.get("source_version", "public-api.v1")),
            missing_fields=tuple(payload.get("missing_fields", ())),
            error=payload.get("error"),
        )


@dataclass(frozen=True, slots=True)
class DerivativesFeaturePolicy:
    """Conservative thresholds for a diagnostic, not a trading strategy."""

    min_venues: int = 2
    max_age: timedelta = timedelta(hours=2)
    max_history_gap: timedelta = timedelta(hours=3)
    min_price_change: Decimal = Decimal("0.002")
    min_open_interest_change: Decimal = Decimal("0.01")
    crowded_funding_per_hour: Decimal = Decimal("0.00001")


@dataclass(frozen=True, slots=True)
class DerivativesFeatures:
    symbol: str
    as_of: datetime
    status: str
    venues: tuple[str, ...]
    fresh_venue_count: int
    price_change_1h: Decimal | None
    open_interest_change_1h: Decimal | None
    open_interest_change_4h: Decimal | None
    open_interest_change_24h: Decimal | None
    funding_rate_per_hour: Decimal | None
    funding_dispersion_per_hour: Decimal | None
    missing_fields: tuple[str, ...]
    source_timestamps: dict[str, str]
    feature_version: str = DERIVATIVES_FEATURE_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of"] = _iso(self.as_of)
        for name in (
            "price_change_1h",
            "open_interest_change_1h",
            "open_interest_change_4h",
            "open_interest_change_24h",
            "funding_rate_per_hour",
            "funding_dispersion_per_hour",
        ):
            if data[name] is not None:
                data[name] = str(data[name])
        return data


@dataclass(frozen=True, slots=True)
class DerivativesRegime:
    symbol: str
    as_of: datetime
    label: str
    price_oi_quadrant: str
    funding_state: str
    score: Decimal
    confidence: Decimal
    status: str
    no_trade: bool
    reason: str
    feature_version: str = DERIVATIVES_FEATURE_VERSION
    regime_version: str = DERIVATIVES_REGIME_VERSION
    mode: str = "shadow"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of"] = _iso(self.as_of)
        data["score"] = str(self.score)
        data["confidence"] = str(self.confidence)
        return data


def _price(observation: DerivativesObservation) -> Decimal | None:
    return observation.index_price or observation.mark_price


def _usable(observation: DerivativesObservation) -> bool:
    return observation.status in {"fresh", "degraded"} and observation.error is None


def _change(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None or previous <= 0:
        return None
    return current / previous - Decimal("1")


def _history_value(
    observations: list[DerivativesObservation],
    *,
    venue: str,
    cutoff: datetime,
    max_gap: timedelta,
) -> DerivativesObservation | None:
    candidates = [
        item
        for item in observations
        if item.venue == venue
        and _usable(item)
        and item.effective_timestamp <= cutoff
        and item.effective_timestamp >= cutoff - max_gap
    ]
    return max(candidates, key=lambda item: item.effective_timestamp) if candidates else None


def build_derivatives_features(
    observations: Iterable[DerivativesObservation],
    *,
    as_of: datetime,
    policy: DerivativesFeaturePolicy | None = None,
) -> dict[str, DerivativesFeatures]:
    """Build point-in-time cross-venue features without using future snapshots."""

    policy = policy or DerivativesFeaturePolicy()
    as_of = _utc(as_of)
    ordered = list(observations)
    symbols = sorted({item.symbol for item in ordered})
    result: dict[str, DerivativesFeatures] = {}
    for symbol in symbols:
        symbol_observations = [item for item in ordered if item.symbol == symbol]
        latest_by_venue: dict[str, DerivativesObservation] = {}
        for item in symbol_observations:
            if item.effective_timestamp <= as_of:
                current = latest_by_venue.get(item.venue)
                if current is None or item.effective_timestamp > current.effective_timestamp:
                    latest_by_venue[item.venue] = item

        venues = tuple(sorted(latest_by_venue))
        fresh = [
            item
            for item in latest_by_venue.values()
            if _usable(item) and as_of - item.observed_at <= policy.max_age
        ]
        missing: set[str] = set()
        if not fresh:
            status = DATA_STALE if latest_by_venue else "insufficient_data"
        else:
            if len(fresh) < policy.min_venues:
                status = "degraded"
            else:
                status = "fresh"

        price_changes: list[Decimal] = []
        oi_changes: dict[int, list[Decimal]] = {1: [], 4: [], 24: []}
        funding_values: list[Decimal] = []
        source_timestamps: dict[str, str] = {}
        for current in fresh:
            source_timestamps[current.venue] = current.effective_timestamp.isoformat()
            if _price(current) is None:
                missing.add("price")
            if current.open_interest_usd is None and current.open_interest is None:
                missing.add("open_interest")
            if current.funding_rate is not None:
                if current.funding_interval_hours is None or current.funding_interval_hours <= 0:
                    missing.add("funding_interval_hours")
                else:
                    funding_values.append(current.funding_rate / current.funding_interval_hours)
            for hours in (1, 4, 24):
                previous = _history_value(
                    symbol_observations,
                    venue=current.venue,
                    cutoff=current.effective_timestamp - timedelta(hours=hours),
                    max_gap=policy.max_history_gap,
                )
                if previous is None:
                    missing.add(f"history_{hours}h")
                    continue
                if hours == 1:
                    value = _change(_price(current), _price(previous))
                    if value is not None:
                        price_changes.append(value)
                current_oi = current.open_interest_usd or current.open_interest
                previous_oi = previous.open_interest_usd or previous.open_interest
                value = _change(current_oi, previous_oi)
                if value is not None:
                    oi_changes[hours].append(value)

        price_change = _median_decimal(price_changes)
        oi_change_1h = _median_decimal(oi_changes[1])
        oi_change_4h = _median_decimal(oi_changes[4])
        oi_change_24h = _median_decimal(oi_changes[24])
        funding = _median_decimal(funding_values)
        dispersion = None
        if funding_values:
            dispersion = max(funding_values) - min(funding_values)
        if fresh and len(fresh) >= policy.min_venues and price_change is not None and oi_change_1h is not None:
            status = "fresh" if not missing else "degraded"
        elif fresh and status == "fresh":
            status = "degraded"
        result[symbol] = DerivativesFeatures(
            symbol=symbol,
            as_of=as_of,
            status=status,
            venues=venues,
            fresh_venue_count=len(fresh),
            price_change_1h=price_change,
            open_interest_change_1h=oi_change_1h,
            open_interest_change_4h=oi_change_4h,
            open_interest_change_24h=oi_change_24h,
            funding_rate_per_hour=funding,
            funding_dispersion_per_hour=dispersion,
            missing_fields=tuple(sorted(missing)),
            source_timestamps=source_timestamps,
        )
    return result


def classify_derivatives_regime(
    features: DerivativesFeatures,
    *,
    policy: DerivativesFeaturePolicy | None = None,
) -> DerivativesRegime:
    """Classify a coarse diagnostic regime; it does not emit a trade signal."""

    policy = policy or DerivativesFeaturePolicy()
    price = features.price_change_1h
    oi = features.open_interest_change_1h
    if (
        features.status in {DATA_STALE, "insufficient_data"}
        or features.fresh_venue_count < policy.min_venues
        or price is None
        or oi is None
    ):
        return DerivativesRegime(
            symbol=features.symbol,
            as_of=features.as_of,
            label="insufficient_data",
            price_oi_quadrant="unknown",
            funding_state="unknown",
            score=Decimal("0"),
            confidence=Decimal("0"),
            status=features.status,
            no_trade=True,
            reason="derivatives data is stale, incomplete, or below the venue quorum",
        )

    price_sign = 1 if price >= policy.min_price_change else -1 if price <= -policy.min_price_change else 0
    oi_sign = 1 if oi >= policy.min_open_interest_change else -1 if oi <= -policy.min_open_interest_change else 0
    quadrant = {
        (1, 1): "price_up_oi_up",
        (-1, 1): "price_down_oi_up",
        (1, -1): "price_up_oi_down",
        (-1, -1): "price_down_oi_down",
    }.get((price_sign, oi_sign), "mixed_or_flat")
    label = {
        "price_up_oi_up": "leveraged_long_expansion",
        "price_down_oi_up": "leveraged_short_expansion",
        "price_up_oi_down": "short_covering",
        "price_down_oi_down": "deleveraging",
    }.get(quadrant, "neutral")
    funding = features.funding_rate_per_hour
    if funding is None:
        funding_state = "unknown"
    elif funding >= policy.crowded_funding_per_hour:
        funding_state = "positive_crowding"
    elif funding <= -policy.crowded_funding_per_hour:
        funding_state = "negative_crowding"
    else:
        funding_state = "normal"
    score = Decimal(str((price_sign + oi_sign) / 2))
    if funding_state == "positive_crowding":
        score += Decimal("0.1")
    elif funding_state == "negative_crowding":
        score -= Decimal("0.1")
    confidence = min(Decimal("1"), Decimal("0.5") + Decimal("0.25") * min(features.fresh_venue_count, 2))
    return DerivativesRegime(
        symbol=features.symbol,
        as_of=features.as_of,
        label=label,
        price_oi_quadrant=quadrant,
        funding_state=funding_state,
        score=score,
        confidence=confidence,
        status=features.status,
        no_trade=False,
        reason="shadow diagnostic only; canonical strategy and Operations decision are unchanged",
    )


def build_derivatives_shadow_report(
    observations: Iterable[DerivativesObservation],
    *,
    as_of: datetime,
    policy: DerivativesFeaturePolicy | None = None,
) -> dict[str, Any]:
    """Build the public report consumed by research review, not by execution."""

    observations = list(observations)
    features = build_derivatives_features(observations, as_of=as_of, policy=policy)
    regimes = {symbol: classify_derivatives_regime(item, policy=policy) for symbol, item in features.items()}
    return {
        "schema": "crypto.derivatives-shadow.v1",
        "mode": "shadow",
        "canonical_strategy_changed": False,
        "generated_at": _iso(_utc(as_of)),
        "observation_count": len(observations),
        "observations": [item.to_dict() for item in observations],
        "features": {symbol: item.to_dict() for symbol, item in features.items()},
        "regimes": {symbol: item.to_dict() for symbol, item in regimes.items()},
    }
