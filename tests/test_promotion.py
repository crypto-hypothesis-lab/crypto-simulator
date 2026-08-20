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
