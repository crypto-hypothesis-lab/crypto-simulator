import json
from pathlib import Path

import pytest

from crypto_simulator.strategy_registry import (
    SCHEMA_VERSION,
    StrategyDefinition,
    build_strategy_instance_id,
    load_strategy_registry,
    parameters_hash,
    select_strategies,
)


def definition(**overrides):
    value = {
        "schema_version": SCHEMA_VERSION,
        "strategy_id": "event_long",
        "strategy_version": "v1",
        "parameters": {"threshold": 2},
        "venues": ["mexc"],
        "market_types": ["perpetual"],
        "interval": "1hour",
        "required_features": ["atr", "regime"],
        "allowed_directions": ["LONG"],
        "max_leverage": "1",
        "stage": "paper",
        "enabled": True,
    }
    value.update(overrides)
    return value


def test_registry_identity_changes_when_version_or_parameters_change() -> None:
    base = StrategyDefinition.from_mapping(definition())
    changed = StrategyDefinition.from_mapping(definition(strategy_version="v2"))
    assert base.parameters_hash == parameters_hash({"threshold": 2})
    assert base.instance_id(venue="mexc", market_type="perpetual", mode="paper") != changed.instance_id(venue="mexc", market_type="perpetual", mode="paper")
    assert base.instance_id(venue="mexc", market_type="perpetual", mode="paper") == build_strategy_instance_id(
        venue="mexc", market_type="perpetual", strategy_id="event_long", strategy_version="v1",
        parameters_hash_value=base.parameters_hash, mode="paper",
    )


def test_registry_selects_only_enabled_matching_stage() -> None:
    enabled = StrategyDefinition.from_mapping(definition())
    disabled = StrategyDefinition.from_mapping(definition(strategy_id="disabled", enabled=False))
    other_stage = StrategyDefinition.from_mapping(definition(strategy_id="research", stage="forward_test"))
    assert select_strategies([enabled, disabled, other_stage], venue="mexc", market_type="perpetual", mode="paper") == [enabled]


def test_registry_rejects_leverage_above_hard_cap() -> None:
    with pytest.raises(ValueError, match="at most 5"):
        StrategyDefinition.from_mapping(definition(max_leverage="5.1"))


def test_registry_loads_json_files_and_rejects_duplicate_identity(tmp_path: Path) -> None:
    path = tmp_path / "one.json"
    path.write_text(json.dumps(definition()), encoding="utf-8")
    assert len(load_strategy_registry(tmp_path, enabled_only=True)) == 1
    (tmp_path / "two.json").write_text(json.dumps(definition()), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate strategy identity"):
        load_strategy_registry(tmp_path)
