from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from statistics import mean
from typing import Any, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    """Causal gate for moving a research candidate into Paper.

    Values are fractions, so ``0.002`` means +0.20%.  Outcomes must be ordered
    by the time at which they became available; the gate does not reorder them
    using future performance information.
    """

    windows: tuple[int, ...] = (20, 50, 100)
    cost_reserve: float = 0.0051
    minimum_net_effective_ev: float = 0.002
    minimum_filled: int = 20
    minimum_fill_rate: float = 0.80
    minimum_distinct_days: int = 30
    lower_confidence_z: float = 1.96

    def __post_init__(self) -> None:
        if not self.windows or any(window <= 0 for window in self.windows):
            raise ValueError("promotion windows must be positive")
        if tuple(sorted(set(self.windows))) != self.windows:
            raise ValueError("promotion windows must be sorted and unique")
        if self.cost_reserve < 0 or self.minimum_net_effective_ev < 0:
            raise ValueError("cost reserve and minimum EV must not be negative")
        if self.minimum_filled <= 0 or not 0 < self.minimum_fill_rate <= 1:
            raise ValueError("minimum fill constraints are invalid")
        if self.minimum_distinct_days <= 0 or self.lower_confidence_z <= 0:
            raise ValueError("promotion confidence constraints are invalid")


def _timestamp(row: Mapping[str, Any]) -> str:
    for key in ("available_at", "exit_timestamp", "timestamp", "event_time"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return ""


def _day(row: Mapping[str, Any]) -> str:
    return _timestamp(row)[:10]


def _return_fraction(row: Mapping[str, Any]) -> float | None:
    if row.get("filled") is False:
        return None
    for key in ("return_fraction", "net_return_fraction", "pnl_fraction", "net_pnl_fraction"):
        value = row.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if isfinite(parsed) else None
    return None


def _window_result(rows: Sequence[Mapping[str, Any]], policy: PromotionPolicy) -> dict[str, Any]:
    values = [value for row in rows if (value := _return_fraction(row)) is not None]
    filled = len(values)
    observed = len(rows)
    fill_rate = filled / observed if observed else 0.0
    average_return = mean(values) if values else None
    net_values = [value - policy.cost_reserve for value in values]
    net_ev = fill_rate * (average_return - policy.cost_reserve) if average_return is not None else None
    lower95 = None
    if len(net_values) >= 2:
        average_net = mean(net_values)
        variance = sum((value - average_net) ** 2 for value in net_values) / (len(net_values) - 1)
        lower95 = average_net - policy.lower_confidence_z * sqrt(variance / len(net_values))
    required_filled = max(policy.minimum_filled, int((observed * policy.minimum_fill_rate) + 0.999999))
    passed = (
        observed > 0
        and filled >= required_filled
        and fill_rate >= policy.minimum_fill_rate
        and net_ev is not None
        and net_ev >= policy.minimum_net_effective_ev
    )
    return {
        "observed": observed,
        "filled": filled,
        "required_filled": required_filled,
        "fill_rate": fill_rate,
        "average_return": average_return,
        "net_effective_ev": net_ev,
        "lower95_net_return": lower95,
        "passed": passed,
    }


def evaluate_promotion_gate(
    outcomes: Sequence[Mapping[str, Any]],
    policy: PromotionPolicy | None = None,
) -> dict[str, Any]:
    """Evaluate a candidate using only availability-bounded outcomes.

    Each outcome should contain ``filled`` and a net return fraction.  A false
    ``filled`` row remains in the denominator, so attractive but rarely filled
    limit orders cannot pass by reporting only their successful fills.
    """

    policy = policy or PromotionPolicy()
    rows = sorted((dict(row) for row in outcomes), key=_timestamp)
    window_results = []
    for window in policy.windows:
        sample = rows[-window:]
        result = _window_result(sample, policy)
        result["window"] = window
        result["distinct_days"] = len({_day(row) for row in sample if _day(row)})
        window_results.append(result)

    full = _window_result(rows, policy)
    distinct_days = len({_day(row) for row in rows if _day(row)})
    all_windows_passed = bool(window_results) and all(item["passed"] for item in window_results)
    full_lower95 = full["lower95_net_return"]
    passed = (
        len(rows) >= max(policy.windows)
        and all_windows_passed
        and distinct_days >= policy.minimum_distinct_days
        and full_lower95 is not None
        and full_lower95 >= 0.0
    )
    return {
        "decision": "candidate" if passed else "hold",
        "policy": asdict(policy),
        "full_sample": {**full, "distinct_days": distinct_days},
        "windows": window_results,
        "outcome_count": len(rows),
        "distinct_days": distinct_days,
        "reason": None if passed else "promotion_filters_not_passed",
    }
