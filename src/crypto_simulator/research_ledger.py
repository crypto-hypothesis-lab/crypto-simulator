"""Durable, queryable archive for successful and failed research artifacts.

The ledger stores the original JSON artifact alongside small normalized tables.
That keeps every failed hypothesis reproducible without forcing OHLCV history or
large databases into Git.  Import is content-addressed and therefore idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


LEDGER_SCHEMA_VERSION = "crypto.research-ledger.v2"
FAILURE_SCHEMA_VERSION = "crypto.research-failure.v2"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _strategy_identity(row: Mapping[str, Any]) -> tuple[str, str]:
    strategy = row.get("strategy")
    if isinstance(strategy, Mapping):
        strategy_id = str(strategy.get("strategy_id") or strategy.get("name") or "unknown")
        version = str(strategy.get("strategy_version") or strategy.get("name") or strategy_id)
        return strategy_id, version
    if strategy:
        return str(strategy), str(row.get("strategy_version") or strategy)
    strategy_id = str(row.get("strategy_id") or row.get("name") or "unknown")
    return strategy_id, str(row.get("strategy_version") or strategy_id)


def _artifact_type(payload: Mapping[str, Any]) -> str:
    return str(payload.get("schema_version") or payload.get("schema") or payload.get("report_type") or "research-report")


def _status(payload: Mapping[str, Any]) -> str:
    summary = _mapping(payload.get("summary"))
    return str(summary.get("status") or payload.get("status") or payload.get("decision") or "unknown")


def _strategy_rows(payload: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any], str]]:
    rows: list[tuple[str, str, Mapping[str, Any], str]] = []
    full_sample = payload.get("full_sample")
    if isinstance(full_sample, list):
        for item in full_sample:
            if not isinstance(item, Mapping):
                continue
            strategy_id, version = _strategy_identity(item)
            metrics = _mapping(item.get("metrics"))
            trades = int(metrics.get("round_trips") or metrics.get("trades") or item.get("event_count") or 0)
            if item.get("measurement_status"):
                outcome = str(item["measurement_status"])
            else:
                outcome = "unmeasured" if trades == 0 else "full_sample_positive" if float(metrics.get("total_return") or 0) > 0 else "full_sample_negative_or_flat"
            rows.append((strategy_id, version, metrics, outcome))
    if not rows and payload.get("strategy"):
        strategy_id, version = _strategy_identity(payload)
        rows.append((strategy_id, version, _mapping(payload.get("metrics")), _status(payload)))
    return rows


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _failure_reason(
    reason_code: str,
    *,
    message: str,
    category: str = "diagnostic",
    scope: str = "run",
    strategy_id: str | None = None,
    severity: str = "warning",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "reason_code": reason_code,
        "category": category,
        "scope": scope,
        "strategy_id": strategy_id,
        "severity": severity,
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _explicit_failure_reasons(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("failure_reasons")
    if values is None:
        values = _mapping(payload.get("diagnostics")).get("failure_reasons")
    if not isinstance(values, list):
        return []
    reasons: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, Mapping):
            code = str(value.get("reason_code") or value.get("code") or "explicit_failure")
            reasons.append(
                _failure_reason(
                    code,
                    message=str(value.get("message") or value.get("reason") or code),
                    category=str(value.get("category") or "diagnostic"),
                    scope=str(value.get("scope") or "run"),
                    strategy_id=str(value["strategy_id"]) if value.get("strategy_id") else None,
                    severity=str(value.get("severity") or "warning"),
                    evidence=_mapping(value.get("evidence")),
                )
            )
        elif value:
            code = str(value)
            reasons.append(_failure_reason(code, message=code))
    return reasons


def derive_failure_reasons(
    payload: Mapping[str, Any],
    strategy_rows: list[tuple[str, str, Mapping[str, Any], str]] | None = None,
) -> list[dict[str, Any]]:
    """Derive conservative, reviewable reasons a research run was not promoted.

    This is deliberately diagnostic rather than a profitability classifier.  A
    no-trade window is never treated as a win, and an event study with too few
    observations is recorded as insufficient evidence rather than a strategy
    failure.
    """

    summary = _mapping(payload.get("summary"))
    dataset = _mapping(payload.get("dataset"))
    quality = _mapping(dataset.get("quality"))
    status = _status(payload)
    rows = strategy_rows if strategy_rows is not None else _strategy_rows(payload)
    reasons = _explicit_failure_reasons(payload)

    def add(reason: dict[str, Any]) -> None:
        key = (reason["reason_code"], reason.get("scope"), reason.get("strategy_id"))
        if not any((item["reason_code"], item.get("scope"), item.get("strategy_id")) == key for item in reasons):
            reasons.append(reason)

    quality_status = str(summary.get("data_quality_status") or "")
    if quality_status and quality_status != "complete":
        add(
            _failure_reason(
                "data_quality_incomplete",
                message="The dataset was not complete enough for a reliable promotion decision.",
                category="data_blocked",
                severity="blocker",
                evidence={"data_quality_status": quality_status},
            )
        )
    if quality.get("contiguous") is False or quality.get("missing_bars", 0):
        add(
            _failure_reason(
                "data_quality_gaps",
                message="Missing or non-contiguous candles were present.",
                category="data_blocked",
                severity="blocker",
                evidence={
                    "contiguous": quality.get("contiguous"),
                    "missing_bars": quality.get("missing_bars"),
                    "gap_count": quality.get("gap_count"),
                },
            )
        )

    positive_oos = _number(summary.get("positive_oos_windows"))
    negative_oos = _number(summary.get("negative_oos_windows"))
    flat_oos = _number(summary.get("flat_or_no_trade_oos_windows"))
    no_qualified = _number(summary.get("no_qualified_strategy_windows"))
    if positive_oos == 0 and summary.get("walk_forward_windows"):
        add(
            _failure_reason(
                "no_positive_oos_windows",
                message="No Walk Forward test window produced a positive return.",
                category="validation_blocked",
                severity="blocker",
                evidence={"positive_oos_windows": positive_oos, "walk_forward_windows": summary.get("walk_forward_windows")},
            )
        )
    if negative_oos and negative_oos > 0:
        add(
            _failure_reason(
                "negative_oos_window_present",
                message="At least one out-of-sample window lost money.",
                category="negative_performance",
                severity="warning",
                evidence={"negative_oos_windows": negative_oos},
            )
        )
    if flat_oos and flat_oos > 0:
        add(
            _failure_reason(
                "flat_or_no_trade_oos_windows",
                message="Some out-of-sample windows had no qualifying trade or were flat; they are not wins.",
                category="not_measured",
                severity="warning",
                evidence={"flat_or_no_trade_oos_windows": flat_oos},
            )
        )
    if no_qualified and no_qualified > 0:
        add(
            _failure_reason(
                "no_qualified_training_candidate",
                message="The training window had no candidate passing the minimum research floors.",
                category="validation_blocked",
                severity="warning",
                evidence={"no_qualified_strategy_windows": no_qualified},
            )
        )

    event_count = _number(summary.get("event_count"))
    measurable = _number(summary.get("measurable_strategy_count"))
    if status == "event_study_only" and (measurable == 0 or (event_count is not None and event_count < 50)):
        add(
            _failure_reason(
                "insufficient_event_count",
                message="The market-structure event study has too few events for a promotion decision.",
                category="not_measured",
                severity="blocker",
                evidence={"event_count": event_count, "measurable_strategy_count": measurable},
            )
        )

    for strategy_id, _version, metrics, outcome in rows:
        trades = _number(metrics.get("round_trips") or metrics.get("trades") or metrics.get("event_count")) or 0
        total_return = _number(metrics.get("total_return"))
        profit_factor = _number(metrics.get("profit_factor"))
        expectancy = _number(metrics.get("expectancy_per_trade"))
        if outcome == "unmeasured" or trades == 0:
            add(
                _failure_reason(
                    "no_observed_trades",
                    message="The strategy produced no observed round trips/events; performance is unmeasured.",
                    category="not_measured",
                    scope="strategy",
                    strategy_id=strategy_id,
                    severity="warning",
                    evidence={"trades": trades, "measurement_status": outcome},
                )
            )
        if outcome == "full_sample_negative_or_flat" or (total_return is not None and total_return <= 0 and trades > 0):
            add(
                _failure_reason(
                    "negative_or_flat_full_sample",
                    message="The full-sample result was not profitable after the recorded assumptions.",
                    category="negative_performance",
                    scope="strategy",
                    strategy_id=strategy_id,
                    severity="warning",
                    evidence={"total_return": total_return, "trades": trades},
                )
            )
        if trades >= 20 and profit_factor is not None and profit_factor < 1.15:
            add(
                _failure_reason(
                    "profit_factor_below_research_floor",
                    message="Profit Factor remained below the 1.15 research floor.",
                    category="weak_performance",
                    scope="strategy",
                    strategy_id=strategy_id,
                    severity="warning",
                    evidence={"profit_factor": profit_factor, "floor": 1.15, "trades": trades},
                )
            )
        if trades >= 20 and expectancy is not None and expectancy <= 0:
            add(
                _failure_reason(
                    "non_positive_expectancy",
                    message="Post-cost expectancy was not positive.",
                    category="negative_performance",
                    scope="strategy",
                    strategy_id=strategy_id,
                    severity="warning",
                    evidence={"expectancy_per_trade": expectancy, "trades": trades},
                )
            )

    if status in {"not_validated", "low_statistical_power", "full_sample_only"} and not reasons:
        add(
            _failure_reason(
                "promotion_not_validated",
                message="The run did not meet the required validation stage.",
                category="validation_blocked",
                severity="blocker",
                evidence={"status": status},
            )
        )
    return reasons


@dataclass(frozen=True, slots=True)
class ResearchRunSummary:
    run_id: str
    experiment_id: str
    recorded_at: str
    artifact_type: str
    exchange: str | None
    market: str | None
    stage: str
    status: str
    strategy_ids: tuple[str, ...]
    dataset_start: str | None
    dataset_end: str | None


class DuckDbResearchLedger:
    """Content-addressed DuckDB ledger for research evidence and failures."""

    def __init__(self, path: str | Path) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Install crypto-simulator[analysis] to use the research ledger") from exc
        self._duckdb = duckdb
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._duckdb.connect(str(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id VARCHAR PRIMARY KEY,
                    experiment_id VARCHAR NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL,
                    artifact_generated_at VARCHAR,
                    artifact_type VARCHAR NOT NULL,
                    exchange VARCHAR,
                    market VARCHAR,
                    stage VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    dataset_start VARCHAR,
                    dataset_end VARCHAR,
                    symbols_json VARCHAR NOT NULL,
                    strategy_ids_json VARCHAR NOT NULL,
                    tags_json VARCHAR NOT NULL,
                    hypothesis VARCHAR,
                    conclusion VARCHAR,
                    source_path VARCHAR,
                    content_sha256 VARCHAR NOT NULL,
                    payload_json VARCHAR NOT NULL,
                    ledger_schema_version VARCHAR NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_failure_reasons (
                    run_id VARCHAR NOT NULL,
                    reason_code VARCHAR NOT NULL,
                    scope VARCHAR NOT NULL,
                    strategy_id VARCHAR,
                    category VARCHAR NOT NULL DEFAULT 'diagnostic',
                    severity VARCHAR NOT NULL,
                    message VARCHAR NOT NULL,
                    evidence_json VARCHAR NOT NULL,
                    PRIMARY KEY (run_id, reason_code, scope, strategy_id)
                )
                """
            )
            connection.execute(
                "ALTER TABLE research_failure_reasons ADD COLUMN IF NOT EXISTS category VARCHAR DEFAULT 'diagnostic'"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_strategy_results (
                    run_id VARCHAR NOT NULL,
                    strategy_id VARCHAR NOT NULL,
                    strategy_version VARCHAR NOT NULL,
                    outcome_status VARCHAR NOT NULL,
                    metrics_json VARCHAR NOT NULL,
                    PRIMARY KEY (run_id, strategy_id, strategy_version)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS research_walk_forward_windows (
                    run_id VARCHAR NOT NULL,
                    window_index INTEGER NOT NULL,
                    selected_strategy VARCHAR,
                    test_start VARCHAR,
                    test_end VARCHAR,
                    trade_count INTEGER NOT NULL,
                    total_return DOUBLE,
                    excess_return DOUBLE,
                    outcome_status VARCHAR NOT NULL,
                    train_metrics_json VARCHAR NOT NULL,
                    test_metrics_json VARCHAR NOT NULL,
                    PRIMARY KEY (run_id, window_index)
                )
                """
            )
            # Backfill the derived diagnostic table for ledgers created before
            # failure reasons were added. The original payload remains the
            # source of truth and content-addressed run IDs keep this idempotent.
            existing_runs = connection.execute(
                "SELECT run_id, payload_json FROM research_runs"
            ).fetchall()
            for existing_run_id, payload_json in existing_runs:
                try:
                    existing_payload = json.loads(payload_json)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(existing_payload, Mapping):
                    continue
                for reason in derive_failure_reasons(existing_payload):
                    connection.execute(
                        """
                        INSERT INTO research_failure_reasons
                            (run_id, reason_code, scope, strategy_id, category, severity, message, evidence_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (run_id, reason_code, scope, strategy_id) DO UPDATE SET
                            category = EXCLUDED.category,
                            severity = EXCLUDED.severity,
                            message = EXCLUDED.message,
                            evidence_json = EXCLUDED.evidence_json
                        """,
                        (
                            existing_run_id,
                            reason["reason_code"],
                            reason["scope"],
                            reason.get("strategy_id") or "",
                            reason["category"],
                            reason["severity"],
                            reason["message"],
                            _json(reason.get("evidence") or {}),
                        ),
                    )

    def record(
        self,
        payload: Mapping[str, Any],
        *,
        experiment_id: str | None = None,
        stage: str = "backtest",
        exchange: str | None = None,
        hypothesis: str | None = None,
        conclusion: str | None = None,
        tags: tuple[str, ...] = (),
        source_path: str | None = None,
        recorded_at: datetime | None = None,
    ) -> ResearchRunSummary:
        self.initialize()
        canonical = _json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        run_id = f"research:{digest[:24]}"
        dataset = _mapping(payload.get("dataset"))
        method = _mapping(payload.get("method"))
        inferred_exchange = exchange or (str(dataset.get("exchange")) if dataset.get("exchange") else None)
        market = str(dataset.get("market") or method.get("market") or "") or None
        strategy_rows = _strategy_rows(payload)
        strategy_ids = tuple(sorted({item[0] for item in strategy_rows}))
        experiment_id = experiment_id or f"{_artifact_type(payload)}:{','.join(strategy_ids) or 'unclassified'}"
        recorded_at = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        symbols = dataset.get("symbols") if isinstance(dataset.get("symbols"), list) else []

        with self._duckdb.connect(str(self.path)) as connection:
            connection.execute(
                """
                INSERT INTO research_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id) DO UPDATE SET
                    recorded_at = EXCLUDED.recorded_at,
                    experiment_id = EXCLUDED.experiment_id,
                    stage = EXCLUDED.stage,
                    tags_json = EXCLUDED.tags_json,
                    hypothesis = COALESCE(EXCLUDED.hypothesis, research_runs.hypothesis),
                    conclusion = COALESCE(EXCLUDED.conclusion, research_runs.conclusion),
                    source_path = COALESCE(EXCLUDED.source_path, research_runs.source_path)
                """,
                (
                    run_id,
                    experiment_id,
                    recorded_at,
                    payload.get("generated_at") or payload.get("updated_at"),
                    _artifact_type(payload),
                    inferred_exchange,
                    market,
                    stage,
                    _status(payload),
                    dataset.get("start"),
                    dataset.get("end"),
                    _json(symbols),
                    _json(strategy_ids),
                    _json(sorted(set(tags))),
                    hypothesis,
                    conclusion,
                    source_path,
                    digest,
                    canonical,
                    LEDGER_SCHEMA_VERSION,
                ),
            )
            for strategy_id, version, metrics, outcome in strategy_rows:
                connection.execute(
                    """
                    INSERT INTO research_strategy_results VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, strategy_id, strategy_version) DO UPDATE SET
                        outcome_status = EXCLUDED.outcome_status,
                        metrics_json = EXCLUDED.metrics_json
                    """,
                    (run_id, strategy_id, version, outcome, _json(metrics)),
                )
            windows = payload.get("walk_forward")
            if isinstance(windows, list):
                for index, item in enumerate(windows):
                    if not isinstance(item, Mapping):
                        continue
                    test_metrics = _mapping(item.get("test_metrics"))
                    train_metrics = _mapping(item.get("train_metrics"))
                    trades = int(test_metrics.get("round_trips") or test_metrics.get("trades") or 0)
                    total_return = test_metrics.get("total_return")
                    excess_return = test_metrics.get("excess_return")
                    outcome = "no_trade" if trades == 0 else "positive" if float(total_return or 0) > 0 else "negative_or_flat"
                    connection.execute(
                        """
                        INSERT INTO research_walk_forward_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (run_id, window_index) DO UPDATE SET
                            selected_strategy = EXCLUDED.selected_strategy,
                            trade_count = EXCLUDED.trade_count,
                            total_return = EXCLUDED.total_return,
                            excess_return = EXCLUDED.excess_return,
                            outcome_status = EXCLUDED.outcome_status,
                            train_metrics_json = EXCLUDED.train_metrics_json,
                            test_metrics_json = EXCLUDED.test_metrics_json
                        """,
                        (
                            run_id,
                            index,
                            item.get("selected_strategy"),
                            item.get("test_start"),
                            item.get("test_end"),
                            trades,
                            float(total_return) if total_return is not None else None,
                            float(excess_return) if excess_return is not None else None,
                            outcome,
                            _json(train_metrics),
                            _json(test_metrics),
                        ),
                    )
            for reason in derive_failure_reasons(payload, strategy_rows):
                connection.execute(
                    """
                    INSERT INTO research_failure_reasons
                        (run_id, reason_code, scope, strategy_id, category, severity, message, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, reason_code, scope, strategy_id) DO UPDATE SET
                        category = EXCLUDED.category,
                        severity = EXCLUDED.severity,
                        message = EXCLUDED.message,
                        evidence_json = EXCLUDED.evidence_json
                    """,
                    (
                        run_id,
                        reason["reason_code"],
                        reason["scope"],
                        reason.get("strategy_id") or "",
                        reason["category"],
                        reason["severity"],
                        reason["message"],
                        _json(reason.get("evidence") or {}),
                    ),
                )
        return ResearchRunSummary(
            run_id,
            experiment_id,
            recorded_at.isoformat(),
            _artifact_type(payload),
            inferred_exchange,
            market,
            stage,
            _status(payload),
            strategy_ids,
            str(dataset.get("start")) if dataset.get("start") else None,
            str(dataset.get("end")) if dataset.get("end") else None,
        )

    def history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.initialize()
        with self._duckdb.connect(str(self.path)) as connection:
            rows = connection.execute(
                """
                SELECT run_id, experiment_id, recorded_at, artifact_type, exchange,
                       market, stage, status, strategy_ids_json, dataset_start, dataset_end
                FROM research_runs
                ORDER BY recorded_at DESC, run_id
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [
            {
                "run_id": row[0],
                "experiment_id": row[1],
                "recorded_at": row[2].astimezone(timezone.utc).isoformat(),
                "artifact_type": row[3],
                "exchange": row[4],
                "market": row[5],
                "stage": row[6],
                "status": row[7],
                "strategy_ids": json.loads(row[8]),
                "dataset_start": row[9],
                "dataset_end": row[10],
            }
            for row in rows
        ]

    def evidence(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return a compact, Git-friendly evidence catalog for future tests."""

        raw_runs = self.history(limit=max(limit * 4, limit))
        runs: list[dict[str, Any]] = []
        seen_experiments: set[str] = set()
        for run in raw_runs:
            if run["experiment_id"] in seen_experiments:
                continue
            seen_experiments.add(run["experiment_id"])
            runs.append(run)
            if len(runs) >= limit:
                break
        self.initialize()
        with self._duckdb.connect(str(self.path)) as connection:
            for run in runs:
                payload_row = connection.execute(
                    "SELECT payload_json FROM research_runs WHERE run_id = ?",
                    [run["run_id"]],
                ).fetchone()
                payload = json.loads(payload_row[0]) if payload_row else {}
                strategy_rows = connection.execute(
                    """
                    SELECT strategy_id, strategy_version, outcome_status, metrics_json
                    FROM research_strategy_results
                    WHERE run_id = ?
                    ORDER BY strategy_id, strategy_version
                    """,
                    [run["run_id"]],
                ).fetchall()
                window_rows = connection.execute(
                    """
                    SELECT outcome_status, COUNT(*)
                    FROM research_walk_forward_windows
                    WHERE run_id = ?
                    GROUP BY outcome_status
                    ORDER BY outcome_status
                    """,
                    [run["run_id"]],
                ).fetchall()
                failure_rows = connection.execute(
                    """
                    SELECT reason_code, scope, strategy_id, category, severity, message, evidence_json
                    FROM research_failure_reasons
                    WHERE run_id = ?
                    ORDER BY reason_code, scope, strategy_id
                    """,
                    [run["run_id"]],
                ).fetchall()
                run["strategy_results"] = [
                    {
                        "strategy_id": row[0],
                        "strategy_version": row[1],
                        "outcome_status": row[2],
                        "metrics": json.loads(row[3]),
                    }
                    for row in strategy_rows
                ]
                run["walk_forward_outcomes"] = {row[0]: row[1] for row in window_rows}
                all_reasons = [
                    {
                        "reason_code": row[0],
                        "scope": row[1],
                        "strategy_id": row[2] or None,
                        "category": row[3],
                        "severity": row[4],
                        "message": row[5],
                        "evidence": json.loads(row[6]),
                    }
                    for row in failure_rows
                ]
                run["performance_failures"] = [
                    item for item in all_reasons
                    if item["category"] in {"negative_performance", "weak_performance"}
                ]
                run["diagnostics"] = [
                    item for item in all_reasons
                    if item["category"] not in {"negative_performance", "weak_performance"}
                ]
                run["dataset"] = payload.get("dataset")
                run["method"] = payload.get("method")
                run["summary"] = payload.get("summary")
        return runs
