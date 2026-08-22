from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from crypto_simulator.research_ledger import DuckDbResearchLedger


def _artifact() -> dict:
    return {
        "dataset": {
            "market": "perpetual",
            "symbols": ["BTC_USDT", "ETH_USDT"],
            "start": "2025-01-01T00:00:00+00:00",
            "end": "2025-12-31T00:00:00+00:00",
        },
        "method": {"market": "perpetual"},
        "full_sample": [
            {
                "strategy": {"name": "failed_v1"},
                "metrics": {"round_trips": 12, "total_return": -0.12},
            },
            {
                "strategy": {"name": "unmeasured_v1"},
                "metrics": {"round_trips": 0, "total_return": 0.0},
            },
        ],
        "walk_forward": [
            {
                "test_start": "2025-09-01T00:00:00+00:00",
                "test_end": "2025-10-01T00:00:00+00:00",
                "selected_strategy": "failed_v1",
                "train_metrics": {"round_trips": 25},
                "test_metrics": {"round_trips": 0, "total_return": 0.0, "excess_return": 0.0},
            }
        ],
        "summary": {"status": "not_validated"},
    }


def test_ledger_is_idempotent_and_preserves_failures_and_no_trade(tmp_path: Path) -> None:
    ledger = DuckDbResearchLedger(tmp_path / "research.duckdb")
    first = ledger.record(
        _artifact(),
        experiment_id="mexc-structure-baseline",
        hypothesis="price-only entry has positive OOS expectancy",
        conclusion="rejected",
        tags=("mexc", "failure"),
    )
    second = ledger.record(_artifact(), experiment_id="mexc-structure-baseline")
    assert first.run_id == second.run_id
    assert ledger.history() == [
        {
            "run_id": first.run_id,
            "experiment_id": "mexc-structure-baseline",
            "recorded_at": second.recorded_at,
            "artifact_type": "research-report",
            "exchange": None,
            "market": "perpetual",
            "stage": "backtest",
            "status": "not_validated",
            "strategy_ids": ["failed_v1", "unmeasured_v1"],
            "dataset_start": "2025-01-01T00:00:00+00:00",
            "dataset_end": "2025-12-31T00:00:00+00:00",
        }
    ]

    with ledger._duckdb.connect(str(ledger.path)) as connection:
        outcomes = connection.execute(
            "SELECT strategy_id, outcome_status FROM research_strategy_results ORDER BY strategy_id"
        ).fetchall()
        windows = connection.execute(
            "SELECT trade_count, outcome_status FROM research_walk_forward_windows"
        ).fetchall()
    assert outcomes == [("failed_v1", "full_sample_negative_or_flat"), ("unmeasured_v1", "unmeasured")]
    assert windows == [(0, "no_trade")]

    evidence = ledger.evidence()
    reasons = evidence[0]["performance_failures"]
    assert "negative_or_flat_full_sample" in {item["reason_code"] for item in reasons}
    assert any(item["strategy_id"] == "failed_v1" for item in reasons)
    diagnostics = evidence[0]["diagnostics"]
    assert "no_observed_trades" in {item["reason_code"] for item in diagnostics}
