"""Validated, deterministic registry for parallel research/Paper strategies."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "crypto.strategy-instance.v1"
VENUES = frozenset({"bitbank", "hyperliquid", "mexc"})
MARKET_TYPES = frozenset({"spot", "margin", "perpetual"})
DIRECTIONS = frozenset({"LONG", "SHORT"})
STAGES = frozenset({
    "backtest", "walk_forward", "cost_stress", "forward_test", "paper",
    "shadow_live", "small_live", "production", "retired",
})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _required_string(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result or "\n" in result or "\r" in result:
        raise ValueError(f"{field} must be a non-empty single-line string")
    return result


def parameters_hash(parameters: Mapping[str, Any]) -> str:
    if not isinstance(parameters, Mapping):
        raise TypeError("parameters must be an object")
    return sha256(_canonical(dict(parameters)).encode("utf-8")).hexdigest()


def build_strategy_instance_id(
    *, venue: str, market_type: str, strategy_id: str, strategy_version: str,
    parameters_hash_value: str, mode: str,
) -> str:
    identity = "\n".join((venue, market_type, strategy_id, strategy_version, parameters_hash_value, mode))
    return sha256(identity.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    schema_version: str
    strategy_id: str
    strategy_version: str
    parameters: dict[str, Any]
    venues: tuple[str, ...]
    market_types: tuple[str, ...]
    interval: str
    required_features: tuple[str, ...]
    allowed_directions: tuple[str, ...]
    max_leverage: str
    stage: str
    enabled: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "StrategyDefinition":
        if value.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        strategy_id = _required_string(value.get("strategy_id"), "strategy_id")
        strategy_version = _required_string(value.get("strategy_version"), "strategy_version")
        parameters = value.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        venues = tuple(sorted({_required_string(item, "venue").lower() for item in value.get("venues", [])}))
        market_types = tuple(sorted({_required_string(item, "market_type").lower() for item in value.get("market_types", [])}))
        directions = tuple(sorted({_required_string(item, "allowed_direction").upper() for item in value.get("allowed_directions", [])}))
        if not venues or not set(venues).issubset(VENUES):
            raise ValueError("venues must contain only supported venues")
        if not market_types or not set(market_types).issubset(MARKET_TYPES):
            raise ValueError("market_types must contain only supported market types")
        if not directions or not set(directions).issubset(DIRECTIONS):
            raise ValueError("allowed_directions must contain LONG and/or SHORT")
        stage = _required_string(value.get("stage"), "stage").lower()
        if stage not in STAGES:
            raise ValueError(f"unsupported strategy stage: {stage}")
        max_leverage = _required_string(value.get("max_leverage"), "max_leverage")
        try:
            leverage = float(max_leverage)
        except ValueError as exc:
            raise ValueError("max_leverage must be numeric") from exc
        if not 0 < leverage <= 5:
            raise ValueError("max_leverage must be greater than 0 and at most 5")
        return cls(
            schema_version=SCHEMA_VERSION,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            parameters=dict(parameters),
            venues=venues,
            market_types=market_types,
            interval=_required_string(value.get("interval"), "interval"),
            required_features=tuple(sorted({_required_string(item, "required_feature") for item in value.get("required_features", [])})),
            allowed_directions=directions,
            max_leverage=max_leverage,
            stage=stage,
            enabled=bool(value.get("enabled", False)),
        )

    @property
    def parameters_hash(self) -> str:
        return parameters_hash(self.parameters)

    def instance_id(self, *, venue: str, market_type: str, mode: str) -> str:
        if venue not in self.venues or market_type not in self.market_types:
            raise ValueError("venue/market_type is not enabled for this strategy")
        return build_strategy_instance_id(
            venue=venue,
            market_type=market_type,
            strategy_id=self.strategy_id,
            strategy_version=self.strategy_version,
            parameters_hash_value=self.parameters_hash,
            mode=mode,
        )

    def supports(self, *, venue: str, market_type: str, mode: str) -> bool:
        return self.enabled and venue in self.venues and market_type in self.market_types and mode == self.stage

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "parameters": self.parameters,
            "venues": list(self.venues),
            "market_types": list(self.market_types),
            "interval": self.interval,
            "required_features": list(self.required_features),
            "allowed_directions": list(self.allowed_directions),
            "max_leverage": self.max_leverage,
            "stage": self.stage,
            "enabled": self.enabled,
        }


def load_strategy_registry(directory: str | Path, *, enabled_only: bool = False) -> list[StrategyDefinition]:
    root = Path(directory)
    definitions: list[StrategyDefinition] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(root.glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(f"strategy registry file must contain an object: {path}")
        definition = StrategyDefinition.from_mapping(value)
        key = (definition.strategy_id, definition.strategy_version)
        if key in seen:
            raise ValueError(f"duplicate strategy identity: {definition.strategy_id}/{definition.strategy_version}")
        seen.add(key)
        if not enabled_only or definition.enabled:
            definitions.append(definition)
    return definitions


def select_strategies(
    definitions: Iterable[StrategyDefinition], *, venue: str, market_type: str, mode: str,
) -> list[StrategyDefinition]:
    return [item for item in definitions if item.supports(venue=venue, market_type=market_type, mode=mode)]
