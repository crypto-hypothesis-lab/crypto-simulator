"""One-snapshot/many-strategy fan-out without extra market-data fetches."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from hashlib import sha256
import json
from typing import Any

from .feature_snapshot import FeatureSnapshot
from .strategy_registry import StrategyDefinition, select_strategies


Evaluator = Callable[[StrategyDefinition, FeatureSnapshot], Mapping[str, Any]]


def _event_id(snapshot: FeatureSnapshot, instance_id: str, action: str) -> tuple[str, str]:
    signal_key = ":".join((
        snapshot.venue,
        snapshot.symbol,
        snapshot.interval,
        snapshot.candle_close_at.isoformat(),
        instance_id,
        action,
    ))
    return signal_key, sha256(signal_key.encode("utf-8")).hexdigest()


def run_strategy_fanout(
    snapshot: FeatureSnapshot,
    definitions: Iterable[StrategyDefinition],
    *,
    mode: str,
    evaluator: Evaluator,
) -> list[dict[str, Any]]:
    """Evaluate active definitions against one immutable feature snapshot.

    Missing features or degraded data produce explicit `no_trade` records and
    never call the strategy evaluator.  The returned records preserve the
    same lineage fields needed by Simulator, Operations, Notifier, and Site.
    """
    results: list[dict[str, Any]] = []
    for definition in select_strategies(definitions, venue=snapshot.venue, market_type=snapshot.market_type, mode=mode):
        instance_id = definition.instance_id(venue=snapshot.venue, market_type=snapshot.market_type, mode=mode)
        missing = sorted(set(definition.required_features) - set(snapshot.features))
        if snapshot.data_quality != "fresh":
            action = "no_trade"
            reason = "data_not_fresh"
            payload: Mapping[str, Any] = {}
        elif missing:
            action = "no_trade"
            reason = "missing_required_feature"
            payload = {}
        else:
            payload = evaluator(definition, snapshot)
            action = str(payload.get("action") or "no_trade").lower()
            reason = str(payload.get("reason") or "strategy_decision")
            if action not in {"buy", "sell", "hold", "no_trade"}:
                action = "no_trade"
                reason = "invalid_strategy_action"
        signal_key, event_id = _event_id(snapshot, instance_id, action)
        results.append({
            "schema_version": "crypto.signal.v1",
            "signal_source": "crypto-simulator",
            "event_id": event_id,
            "signal_key": signal_key,
            "event_type": "PAPER_SIGNAL",
            "strategy_instance_id": instance_id,
            "strategy_id": definition.strategy_id,
            "strategy_version": definition.strategy_version,
            "exchange": snapshot.venue,
            "symbol": snapshot.symbol,
            "market_type": snapshot.market_type,
            "interval": snapshot.interval,
            "candle_close_at": snapshot.candle_close_at.isoformat(),
            "price": snapshot.features.get("close"),
            "action": action,
            "reason": reason,
            "data_quality": snapshot.data_quality,
            "features_hash": snapshot.bars_hash,
            "payload": dict(payload),
        })
    return results
