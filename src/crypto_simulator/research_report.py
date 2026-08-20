from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


_PROFILE_BY_FAMILY = {
    "mexc_event_long_pullback": "mexc-long",
    "mexc_event_short": "mexc-short",
    "mexc_event_short_rejection_volume": "mexc-short-rejection",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric(metrics: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return metrics.get(key, default)


def _compact_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "total_return",
        "max_drawdown",
        "round_trips",
        "win_rate",
        "profit_factor",
        "expectancy_per_trade",
        "funding_cost_fraction",
        "excess_return",
        "filled_orders",
        "placed_orders",
    )
    return {key: _metric(metrics, key) for key in keys if key in metrics}


def build_research_report(
    report: Mapping[str, Any],
    *,
    exchange: str,
    report_url: str = "",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Convert a simulator research artifact into a safe Paper decision.

    This deliberately promotes only a walk-forward ``candidate_requires_forward_test``
    report with complete data and a known Paper profile. A strong full-sample
    result alone can never start the Paper test.
    """

    if exchange not in {"bitbank", "hyperliquid", "mexc"}:
        raise ValueError("unsupported exchange")
    summary = report.get("summary") or {}
    dataset = report.get("dataset") or {}
    quality = dataset.get("quality") or {}
    full_sample = report.get("full_sample") or []
    if not isinstance(full_sample, list) or not full_sample:
        raise ValueError("research report must contain full_sample")
    best = full_sample[0]
    if not isinstance(best, Mapping):
        raise ValueError("full_sample entry must be an object")
    strategy = best.get("strategy") or {}
    metrics = best.get("metrics") or {}
    if not isinstance(strategy, Mapping) or not isinstance(metrics, Mapping):
        raise ValueError("best strategy and metrics must be objects")

    strategy_name = str(strategy.get("name") or "unknown")
    strategy_family = str(strategy.get("strategy_family") or strategy_name)
    profile = _PROFILE_BY_FAMILY.get(strategy_family)
    status = str(summary.get("status") or "unknown")
    data_quality_status = str(summary.get("data_quality_status") or "unknown")
    data_complete = data_quality_status == "complete" and quality.get("contiguous") is not False
    eligible = status == "candidate_requires_forward_test" and data_complete and profile is not None
    decision = "paper_start" if eligible else "hold"
    if decision == "paper_start":
        reason = "walk_forward_candidate_with_complete_data"
    elif profile is None:
        reason = "unsupported_strategy_family"
    elif not data_complete:
        reason = "dataset_quality_not_complete"
    else:
        reason = f"research_status_{status}"

    end = str(dataset.get("end") or "unknown")
    report_id = f"{exchange}:research:{strategy_name}:{end}"
    walk_forward = []
    for window in report.get("walk_forward") or []:
        if not isinstance(window, Mapping):
            continue
        walk_forward.append(
            {
                "train_start": window.get("train_start"),
                "train_end": window.get("train_end"),
                "test_start": window.get("test_start"),
                "test_end": window.get("test_end"),
                "selected_strategy": window.get("selected_strategy"),
                "test_metrics": _compact_metrics(window.get("test_metrics") or {}),
            }
        )

    return {
        "schema_version": "crypto.research-report.v1",
        "report_id": report_id,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "exchange": exchange,
        "market": dataset.get("market"),
        "report_url": report_url,
        "decision": decision,
        "strategy": {
            "name": strategy_name,
            "strategy_family": strategy_family,
            "profile": profile,
            "max_holding_days": strategy.get("max_holding_days"),
            "max_gross_leverage": strategy.get("max_gross_leverage"),
            "risk_per_trade": strategy.get("risk_per_trade"),
        },
        "summary": {
            "status": status,
            "decision_reason": reason,
            "data_quality_status": data_quality_status,
            "candidate_count": summary.get("candidate_count"),
            "walk_forward_windows": summary.get("walk_forward_windows"),
            "positive_oos_windows": summary.get("positive_oos_windows"),
            "negative_oos_windows": summary.get("negative_oos_windows"),
            "positive_oos_return_fraction": summary.get("positive_oos_return_fraction"),
            "positive_oos_excess_fraction": summary.get("positive_oos_excess_fraction"),
            "median_oos_return": summary.get("median_oos_return"),
            "median_oos_excess_return": summary.get("median_oos_excess_return"),
        },
        "dataset": {
            "symbols": dataset.get("symbols") or [],
            "series_count": dataset.get("series_count"),
            "common_bars": dataset.get("common_bars"),
            "start": dataset.get("start"),
            "end": dataset.get("end"),
            "interval": dataset.get("interval"),
            "quality": quality,
        },
        "performance": _compact_metrics(metrics),
        "walk_forward": walk_forward,
        "paper_test": {
            "status": "start" if eligible else "not_started",
            "mode": "paper_only",
            "max_gross_leverage": min(_number(strategy.get("max_gross_leverage"), 5.0) or 5.0, 5.0),
            "reason": reason,
        },
    }
