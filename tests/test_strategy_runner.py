from datetime import datetime, timezone

from crypto_simulator.feature_snapshot import build_feature_snapshot
from crypto_simulator.models import OHLCVBar
from crypto_simulator.strategy_registry import StrategyDefinition
from crypto_simulator.strategy_runner import run_strategy_fanout


def definition(strategy_id: str, *, required_features: list[str] | None = None) -> StrategyDefinition:
    return StrategyDefinition.from_mapping({
        "schema_version": "crypto.strategy-instance.v1",
        "strategy_id": strategy_id,
        "strategy_version": "v1",
        "parameters": {},
        "venues": ["mexc"],
        "market_types": ["perpetual"],
        "interval": "1hour",
        "required_features": required_features or ["atr", "regime"],
        "allowed_directions": ["LONG"],
        "max_leverage": "1",
        "stage": "paper",
        "enabled": True,
    })


def snapshot():
    start = datetime(2026, 8, 23, tzinfo=timezone.utc)
    bars = [OHLCVBar("mexc", "BTC_USDT", "perpetual", start.replace(day=23 + index // 24, hour=index % 24), 100 + index, 101 + index, 99 + index, 100 + index, 10 + index) for index in range(30)]
    return build_feature_snapshot(bars, interval="1hour", as_of=datetime(2026, 8, 24, 8, tzinfo=timezone.utc))


def test_fanout_evaluates_each_strategy_against_one_snapshot() -> None:
    calls = []

    def evaluator(strategy, feature_snapshot):
        calls.append((strategy.strategy_id, feature_snapshot.bars_hash))
        return {"action": "buy", "reason": "test_entry"}

    records = run_strategy_fanout(snapshot(), [definition("one"), definition("two")], mode="paper", evaluator=evaluator)
    assert [record["strategy_id"] for record in records] == ["one", "two"]
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert records[0]["event_id"] != records[1]["event_id"]


def test_fanout_fails_safe_without_required_feature() -> None:
    records = run_strategy_fanout(snapshot(), [definition("funding", required_features=["funding_rate"])], mode="paper", evaluator=lambda *_: {"action": "buy"})
    assert records[0]["action"] == "no_trade"
    assert records[0]["reason"] == "missing_required_feature"
