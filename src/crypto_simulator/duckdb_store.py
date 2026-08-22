from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .derivatives import DerivativesObservation
from .models import OHLCVBar


class DuckDbCandleStore:
    """Optional local DuckDB store for normalized OHLCV candles."""

    def __init__(self, path: str | Path) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Install crypto-simulator[analysis] to use DuckDB storage") from exc
        self._duckdb = duckdb
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._duckdb.connect(str(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    exchange VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    market_type VARCHAR NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    open DECIMAL(38, 18) NOT NULL,
                    high DECIMAL(38, 18) NOT NULL,
                    low DECIMAL(38, 18) NOT NULL,
                    close DECIMAL(38, 18) NOT NULL,
                    volume DECIMAL(38, 18) NOT NULL,
                    quote_volume DECIMAL(38, 18),
                    PRIMARY KEY (exchange, symbol, market_type, timestamp)
                )
                """
            )

    def upsert(self, bars: list[OHLCVBar]) -> int:
        self.initialize()
        rows = [
            (
                bar.exchange,
                bar.symbol,
                bar.market_type,
                bar.timestamp,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.quote_volume,
            )
            for bar in bars
        ]
        if not rows:
            return 0
        with self._duckdb.connect(str(self.path)) as connection:
            connection.executemany(
                """
                INSERT INTO candles (
                    exchange, symbol, market_type, timestamp,
                    open, high, low, close, volume, quote_volume
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (exchange, symbol, market_type, timestamp) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    quote_volume = EXCLUDED.quote_volume
                """,
                rows,
            )
        return len(rows)

    def load(
        self,
        *,
        exchange: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
    ) -> list[OHLCVBar]:
        self.initialize()
        clauses: list[str] = []
        parameters: list[str] = []
        for field, value in (("exchange", exchange), ("symbol", symbol), ("market_type", market_type)):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._duckdb.connect(str(self.path)) as connection:
            rows = connection.execute(
                f"""
                SELECT exchange, symbol, market_type, timestamp,
                       open, high, low, close, volume, quote_volume
                FROM candles {where}
                ORDER BY timestamp
                """,
                parameters,
            ).fetchall()
        return [
            OHLCVBar(
                exchange=row[0],
                symbol=row[1],
                market_type=row[2],
                timestamp=_as_datetime(row[3]),
                open=row[4],
                high=row[5],
                low=row[6],
                close=row[7],
                volume=row[8],
                quote_volume=row[9],
            )
            for row in rows
        ]


def _as_datetime(value: datetime) -> datetime:
    return value


class DuckDbDerivativesStore:
    """Optional local DuckDB store for normalized derivatives observations."""

    def __init__(self, path: str | Path) -> None:
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Install crypto-simulator[analysis] to use DuckDB storage") from exc
        self._duckdb = duckdb
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._duckdb.connect(str(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS derivatives_observations (
                    venue VARCHAR NOT NULL,
                    symbol VARCHAR NOT NULL,
                    market_type VARCHAR NOT NULL,
                    instrument VARCHAR,
                    observed_at TIMESTAMPTZ NOT NULL,
                    exchange_timestamp TIMESTAMPTZ,
                    published_at TIMESTAMPTZ,
                    mark_price DECIMAL(38, 18),
                    index_price DECIMAL(38, 18),
                    open_interest DECIMAL(38, 18),
                    open_interest_usd DECIMAL(38, 18),
                    funding_rate DECIMAL(38, 18),
                    funding_interval_hours DECIMAL(38, 18),
                    funding_status VARCHAR NOT NULL DEFAULT 'unknown',
                    next_funding_at TIMESTAMPTZ,
                    volume_24h_usd DECIMAL(38, 18),
                    status VARCHAR NOT NULL,
                    source VARCHAR NOT NULL,
                    source_version VARCHAR NOT NULL,
                    missing_fields VARCHAR NOT NULL,
                    error VARCHAR,
                    PRIMARY KEY (venue, symbol, market_type, observed_at)
                )
                """
            )
            connection.execute("ALTER TABLE derivatives_observations ADD COLUMN IF NOT EXISTS instrument VARCHAR")
            connection.execute("ALTER TABLE derivatives_observations ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ")
            connection.execute("ALTER TABLE derivatives_observations ADD COLUMN IF NOT EXISTS funding_status VARCHAR DEFAULT 'unknown'")
            connection.execute("ALTER TABLE derivatives_observations ADD COLUMN IF NOT EXISTS next_funding_at TIMESTAMPTZ")

    def upsert(self, observations: list[DerivativesObservation]) -> int:
        self.initialize()
        rows = [
            (
                item.venue,
                item.symbol,
                item.market_type,
                item.instrument,
                item.observed_at,
                item.exchange_timestamp,
                item.published_at,
                item.mark_price,
                item.index_price,
                item.open_interest,
                item.open_interest_usd,
                item.funding_rate,
                item.funding_interval_hours,
                item.funding_status,
                item.next_funding_at,
                item.volume_24h_usd,
                item.status,
                item.source,
                item.source_version,
                json.dumps(list(item.missing_fields), separators=(",", ":")),
                item.error,
            )
            for item in observations
        ]
        if not rows:
            return 0
        with self._duckdb.connect(str(self.path)) as connection:
            connection.executemany(
                """
                INSERT INTO derivatives_observations (
                    venue, symbol, market_type, instrument, observed_at, exchange_timestamp, published_at,
                    mark_price, index_price, open_interest, open_interest_usd,
                    funding_rate, funding_interval_hours, funding_status, next_funding_at, volume_24h_usd,
                    status, source, source_version, missing_fields, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (venue, symbol, market_type, observed_at) DO UPDATE SET
                    exchange_timestamp = EXCLUDED.exchange_timestamp,
                    published_at = EXCLUDED.published_at,
                    mark_price = EXCLUDED.mark_price,
                    index_price = EXCLUDED.index_price,
                    open_interest = EXCLUDED.open_interest,
                    open_interest_usd = EXCLUDED.open_interest_usd,
                    funding_rate = EXCLUDED.funding_rate,
                    funding_interval_hours = EXCLUDED.funding_interval_hours,
                    funding_status = EXCLUDED.funding_status,
                    next_funding_at = EXCLUDED.next_funding_at,
                    volume_24h_usd = EXCLUDED.volume_24h_usd,
                    status = EXCLUDED.status,
                    source = EXCLUDED.source,
                    source_version = EXCLUDED.source_version,
                    missing_fields = EXCLUDED.missing_fields,
                    error = EXCLUDED.error
                """,
                rows,
            )
        return len(rows)

    def load(
        self,
        *,
        venue: str | None = None,
        symbol: str | None = None,
        market_type: str | None = None,
    ) -> list[DerivativesObservation]:
        self.initialize()
        clauses: list[str] = []
        parameters: list[str] = []
        for field, value in (("venue", venue), ("symbol", symbol.upper() if symbol else None), ("market_type", market_type)):
            if value is not None:
                clauses.append(f"{field} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._duckdb.connect(str(self.path)) as connection:
            rows = connection.execute(
                f"""
                SELECT venue, symbol, market_type, instrument, observed_at, exchange_timestamp, published_at,
                       mark_price, index_price, open_interest, open_interest_usd,
                       funding_rate, funding_interval_hours, funding_status, next_funding_at, volume_24h_usd,
                       status, source, source_version, missing_fields, error
                FROM derivatives_observations {where}
                ORDER BY observed_at
                """,
                parameters,
            ).fetchall()
        return [
            DerivativesObservation(
                venue=row[0],
                symbol=row[1],
                market_type=row[2],
                instrument=row[3],
                observed_at=_as_datetime(row[4]),
                exchange_timestamp=_as_datetime(row[5]) if row[5] is not None else None,
                published_at=_as_datetime(row[6]) if row[6] is not None else None,
                mark_price=row[7],
                index_price=row[8],
                open_interest=row[9],
                open_interest_usd=row[10],
                funding_rate=row[11],
                funding_interval_hours=row[12],
                funding_status=row[13],
                next_funding_at=_as_datetime(row[14]) if row[14] is not None else None,
                volume_24h_usd=row[15],
                status=row[16],
                source=row[17],
                source_version=row[18],
                missing_fields=tuple(json.loads(row[19])) if row[19] else (),
                error=row[20],
            )
            for row in rows
        ]
