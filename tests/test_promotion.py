from datetime import datetime, timedelta, timezone

from crypto_simulator.promotion import PromotionPolicy, evaluate_promotion_gate


def outcome(day: int, *, filled: bool = True, return_fraction: float = 0.01) -> dict[str, object]:
    timestamp = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)
    return {
        "available_at": timestamp.isoformat(),
        "filled": filled,
        "return_fraction": return_fraction if filled else None,
    }


def test_promotion_requires_all_rolling_windows_and_lower_confidence_bound() -> None:
    report = evaluate_promotion_gate([outcome(day) for day in range(100)])
    assert report["decision"] == "candidate"
    assert all(item["passed"] for item in report["windows"])
    assert report["full_sample"]["lower95_net_return"] > 0


def test_unfilled_limit_attempts_stay_in_the_denominator() -> None:
    rows = [outcome(day, filled=day < 15) for day in range(20)]
    report = evaluate_promotion_gate(
        rows,
        PromotionPolicy(windows=(20,), minimum_distinct_days=1),
    )
    assert report["decision"] == "hold"
    assert report["windows"][0]["fill_rate"] < 0.80


def test_promotion_report_exposes_cost_aware_risk_metrics_and_stage() -> None:
    report = evaluate_promotion_gate(
        [outcome(day, return_fraction=0.02 if day % 2 == 0 else -0.005) for day in range(20)],
        PromotionPolicy(windows=(20,), minimum_distinct_days=1),
        stage="forward_test",
    )
    metrics = report["full_sample"]["metrics"]
    assert report["promotion_gate_version"] == "crypto.promotion-gate.v2"
    assert report["stage"] == "forward_test"
    assert report["next_stage"] == "paper"
    assert metrics["expectancy"] is not None
    assert metrics["profit_factor"] > 1
    assert metrics["max_drawdown"] is not None
