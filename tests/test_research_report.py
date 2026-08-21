from crypto_simulator.research_report import build_research_report


def _source(status: str = "not_validated", quality_status: str = "complete"):
    return {
        "dataset": {
            "market": "perpetual",
            "symbols": ["BTC_USDT", "ETH_USDT"],
            "series_count": 2,
            "common_bars": 8760,
            "start": "2025-08-20T00:00:00+00:00",
            "end": "2026-08-20T00:00:00+00:00",
            "interval": "1hour",
            "quality": {"contiguous": quality_status == "complete"},
        },
        "full_sample": [{
            "strategy": {
                "name": "mexc_event_long_pullback_atr_v1",
                "strategy_family": "mexc_event_long_pullback",
                "max_holding_days": 14,
                "max_gross_leverage": 5.0,
                "risk_per_trade": 0.004,
            },
            "metrics": {
                "total_return": 0.12,
                "max_drawdown": 0.04,
                "round_trips": 20,
                "win_rate": 0.6,
                "profit_factor": 1.8,
                "expectancy_per_trade": 0.006,
            },
        }],
        "walk_forward": [],
        "summary": {
            "status": status,
            "data_quality_status": quality_status,
            "candidate_count": 3,
            "walk_forward_windows": 6,
            "positive_oos_windows": 4,
            "negative_oos_windows": 2,
        },
    }


def test_full_sample_profit_does_not_start_paper_when_not_validated():
    result = build_research_report(_source(), exchange="mexc", generated_at="2026-08-20T00:00:00+00:00")

    assert result["decision"] == "hold"
    assert result["paper_test"]["status"] == "not_started"
    assert result["summary"]["decision_reason"] == "research_status_not_validated"


def test_walk_forward_candidate_starts_known_paper_profile():
    result = build_research_report(
        _source(status="candidate_requires_forward_test"),
        exchange="mexc",
        report_url="https://github.com/example/actions/runs/1",
        generated_at="2026-08-20T00:00:00+00:00",
    )

    assert result["decision"] == "paper_start"
    assert result["signal_source"] == "crypto-simulator"
    assert result["strategy"]["strategy_id"] == "mexc_event_long_pullback_atr_v1"
    assert result["strategy"]["strategy_version"] == "mexc_event_long_pullback_atr_v1"
    assert result["strategy"]["profile"] == "mexc-long"
    assert result["paper_test"]["mode"] == "paper_only"


def test_incomplete_data_never_starts_paper():
    result = build_research_report(_source(status="candidate_requires_forward_test", quality_status="gaps"), exchange="mexc")

    assert result["decision"] == "hold"
    assert result["summary"]["decision_reason"] == "dataset_quality_not_complete"
