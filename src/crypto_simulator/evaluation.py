from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from .promotion import summarize_outcomes


EVALUATION_REPORT_SCHEMA_VERSION = "crypto.evaluation-report.v1"


def _outcomes(payload: Mapping[str, Any], label: str) -> list[Mapping[str, Any]]:
    values = payload.get("outcomes")
    if not isinstance(values, list) or not all(isinstance(value, Mapping) for value in values):
        raise ValueError(f"{label} must contain an outcomes array")
    return [dict(value) for value in values]


def _metadata(payload: Mapping[str, Any], label: str) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError(f"{label}.metadata must be an object")
    required = ("strategy_id", "strategy_version")
    missing = [key for key in required if not str(metadata.get(key, ""))]
    if missing:
        raise ValueError(f"{label}.metadata is missing: {', '.join(missing)}")
    return dict(metadata)


def _iso(value: Any) -> str:
    return str(value) if value is not None else ""


def compare_evaluations(
    backtest: Mapping[str, Any],
    forward_test: Mapping[str, Any],
    paper: Mapping[str, Any],
    *,
    cost_reserve: float = 0.0,
    minimum_paper_outcomes: int = 5,
    maximum_expectancy_gap: float = 0.003,
    maximum_profit_factor_drop: float = 0.25,
) -> dict[str, Any]:
    """Compare like-for-like Backtest, Forward Test and Paper outcomes.

    This is a diagnostic and demotion signal, not a live-trading promotion
    command.  Mismatched strategy IDs or versions are rejected so results from
    different code cannot silently be combined.
    """

    sources = {
        "backtest": (backtest, _metadata(backtest, "backtest"), _outcomes(backtest, "backtest")),
        "forward_test": (forward_test, _metadata(forward_test, "forward_test"), _outcomes(forward_test, "forward_test")),
        "paper": (paper, _metadata(paper, "paper"), _outcomes(paper, "paper")),
    }
    reference = sources["backtest"][1]
    identity_keys = ("strategy_id", "strategy_version", "exchange", "symbol", "interval")
    mismatches: list[dict[str, Any]] = []
    for label, (_, metadata, _) in sources.items():
        for key in identity_keys:
            expected = reference.get(key)
            actual = metadata.get(key)
            if expected is not None and actual is not None and actual != expected:
                mismatches.append({"dataset": label, "field": key, "expected": expected, "actual": actual})
    if mismatches:
        return {
            "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
            "status": "rejected_mismatch",
            "metadata": reference,
            "mismatches": mismatches,
            "warning": "evaluation identity does not match",
        }

    metrics = {
        label: summarize_outcomes(rows, cost_reserve=cost_reserve)
        for label, (_, _, rows) in sources.items()
    }
    backtest_metrics = metrics["backtest"]
    paper_metrics = metrics["paper"]
    warnings: list[str] = []
    if paper_metrics["filled"] < minimum_paper_outcomes:
        warnings.append("paper_sample_too_small")
    if backtest_metrics["expectancy"] is not None and paper_metrics["expectancy"] is not None:
        if paper_metrics["expectancy"] < backtest_metrics["expectancy"] - maximum_expectancy_gap:
            warnings.append("paper_expectancy_below_backtest")
    if (
        backtest_metrics["profit_factor"] is not None
        and paper_metrics["profit_factor"] is not None
        and paper_metrics["profit_factor"] < backtest_metrics["profit_factor"] * (1 - maximum_profit_factor_drop)
    ):
        warnings.append("paper_profit_factor_degraded")
    if backtest_metrics["max_drawdown"] is not None and paper_metrics["max_drawdown"] is not None:
        if paper_metrics["max_drawdown"] > backtest_metrics["max_drawdown"] + maximum_expectancy_gap:
            warnings.append("paper_drawdown_worse_than_backtest")

    def delta(field: str) -> float | None:
        left = backtest_metrics.get(field)
        right = paper_metrics.get(field)
        if left is None or right is None:
            return None
        return right - left

    return {
        "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
        "status": "warning" if warnings else "ok",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "metadata": reference,
        "datasets": metrics,
        "deltas_paper_minus_backtest": {
            field: delta(field)
            for field in ("expectancy", "profit_factor", "max_drawdown", "fill_rate", "average_holding_hours", "average_slippage")
        },
        "warnings": warnings,
        "recommendation": "hold_or_demote" if warnings else "continue_observation",
    }
