from crypto_simulator.evaluation import compare_evaluations


def dataset(kind: str, values: list[float]) -> dict:
    return {
        "metadata": {
            "strategy_id": "strategy_a",
            "strategy_version": "strategy_a_v2",
            "exchange": "mexc",
            "symbol": "BTC_USDT",
            "interval": "1hour",
            "dataset": kind,
        },
        "outcomes": [
            {"timestamp": f"2026-08-{index + 1:02d}T00:00:00+00:00", "filled": True, "return_fraction": value}
            for index, value in enumerate(values)
        ],
    }


def test_compare_evaluations_warns_when_paper_reproduction_degrades() -> None:
    report = compare_evaluations(
        dataset("backtest", [0.02, 0.02, 0.01, 0.02, 0.01]),
        dataset("forward_test", [0.01, 0.01, 0.0, 0.01, 0.0]),
        dataset("paper", [-0.02, -0.01, -0.01, -0.02, 0.0]),
        minimum_paper_outcomes=5,
    )
    assert report["status"] == "warning"
    assert "paper_expectancy_below_backtest" in report["warnings"]
    assert report["recommendation"] == "hold_or_demote"


def test_compare_evaluations_rejects_mixed_strategy_versions() -> None:
    paper = dataset("paper", [0.01])
    paper["metadata"]["strategy_version"] = "strategy_a_v3"
    report = compare_evaluations(dataset("backtest", [0.01]), dataset("forward_test", [0.01]), paper)
    assert report["status"] == "rejected_mismatch"
    assert report["mismatches"][0]["field"] == "strategy_version"
