from __future__ import annotations

from datetime import datetime
from pathlib import Path

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
