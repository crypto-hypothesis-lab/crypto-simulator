from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import quote

from ..derivatives import DerivativesObservation
from .http import JsonHttpClient


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _timestamp_ms(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _base_symbol(value: str) -> str:
    normalized = value.upper().replace("/", "-").replace(":USDT", "-USDT").replace("_", "-")
    if normalized.endswith("-SWAP"):
        normalized = normalized[:-5]
    if "-" in normalized:
        return normalized.split("-", 1)[0]
    for suffix in ("USDT", "USDC", "USD"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized


class HyperliquidDerivativesAdapter:
    """Public Hyperliquid perpetual metadata and asset-context adapter."""

    venue = "hyperliquid"
    endpoint = "https://api.hyperliquid.xyz/info"

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient()

    def fetch_snapshots(self, symbols: Iterable[str], *, observed_at: datetime | None = None) -> list[DerivativesObservation]:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        response = self.client.post(self.endpoint, {"type": "metaAndAssetCtxs"})
        if not isinstance(response, list) or len(response) < 2 or not isinstance(response[0], dict):
            raise ValueError("unexpected Hyperliquid metaAndAssetCtxs response")
        universe = response[0].get("universe", [])
        contexts = response[1]
        names = [str(item.get("name", "")).upper() for item in universe]
        by_name = {name: contexts[index] for index, name in enumerate(names) if index < len(contexts) and name}
        result = []
        for symbol in symbols:
            name = symbol.upper()
            context = by_name.get(name)
            if not isinstance(context, dict):
                raise ValueError(f"Hyperliquid perpetual not found: {name}")
            missing = tuple(key for key, value in (("mark_price", context.get("markPx")), ("open_interest", context.get("openInterest")), ("funding_rate", context.get("funding"))) if value in (None, ""))
            result.append(
                DerivativesObservation(
                    venue=self.venue,
                    symbol=name,
                    market_type="perpetual",
                    observed_at=observed_at,
                    mark_price=context.get("markPx"),
                    index_price=context.get("oraclePx"),
                    open_interest=context.get("openInterest"),
                    funding_rate=context.get("funding"),
                    funding_interval_hours=Decimal("1"),
                    volume_24h_usd=context.get("dayNtlVlm"),
                    instrument=name,
                    status="fresh" if not missing else "degraded",
                    source="hyperliquid.metaAndAssetCtxs",
                    missing_fields=missing,
                )
            )
        return result

    def fetch_snapshot(self, symbol: str, *, observed_at: datetime | None = None) -> DerivativesObservation:
        return self.fetch_snapshots([symbol], observed_at=observed_at)[0]


class BybitDerivativesAdapter:
    """Public Bybit V5 linear perpetual ticker adapter."""

    venue = "bybit"
    endpoint = "https://api.bybit.com/v5/market/tickers"

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient()

    def fetch_snapshot(self, symbol: str, *, observed_at: datetime | None = None) -> DerivativesObservation:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        normalized = symbol.replace("/", "").replace(":USDT", "").replace("_", "").replace("-", "").upper()
        if not normalized.endswith(("USDT", "USDC", "USD")):
            normalized = f"{normalized}USDT"
        response = self.client.get(f"{self.endpoint}?category=linear&symbol={quote(normalized)}")
        if not isinstance(response, dict) or response.get("retCode") not in (0, "0"):
            code = response.get("retCode") if isinstance(response, dict) else "unknown"
            message = response.get("retMsg", "unknown") if isinstance(response, dict) else "invalid JSON shape"
            raise ValueError(f"Bybit ticker request failed: code={code} message={message}")
        rows = response.get("result", {}).get("list", [])
        if not rows:
            raise ValueError(f"Bybit perpetual not found: {normalized}")
        row = rows[0]
        fields = {"mark_price": row.get("markPrice"), "open_interest_usd": row.get("openInterestValue"), "funding_rate": row.get("fundingRate")}
        missing = tuple(key for key, value in fields.items() if value in (None, ""))
        return DerivativesObservation(
            venue=self.venue,
            symbol=_base_symbol(normalized),
            market_type="perpetual",
            observed_at=observed_at,
            exchange_timestamp=_timestamp_ms(response.get("time"), observed_at),
            mark_price=row.get("markPrice"),
            index_price=row.get("indexPrice"),
            open_interest=row.get("openInterest"),
            open_interest_usd=row.get("openInterestValue"),
            funding_rate=row.get("fundingRate"),
            funding_interval_hours=row.get("fundingIntervalHour"),
            volume_24h_usd=row.get("turnover24h"),
            instrument=normalized,
            status="fresh" if not missing else "degraded",
            source="bybit.v5.market.tickers",
            missing_fields=missing,
        )


class OkxDerivativesAdapter:
    """Public OKX SWAP ticker, mark-price, OI and funding adapter."""

    venue = "okx"
    base_url = "https://www.okx.com/api/v5"

    def __init__(self, client: JsonHttpClient | None = None) -> None:
        self.client = client or JsonHttpClient()

    def _get_rows(self, path: str, inst_id: str) -> list[dict[str, Any]]:
        response = self.client.get(f"{self.base_url}{path}?instType=SWAP&instId={quote(inst_id)}")
        if not isinstance(response, dict) or str(response.get("code")) != "0":
            raise ValueError(f"unexpected OKX response for {path}")
        rows = response.get("data", [])
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _funding_interval_hours(value: Any) -> Decimal | None:
        parsed = _decimal(value)
        if parsed is None or parsed <= 0:
            return None
        # OKX has returned both hour-like values and millisecond intervals.
        return parsed / Decimal("3600000") if parsed > Decimal("1000") else parsed

    def fetch_snapshot(self, symbol: str, *, observed_at: datetime | None = None) -> DerivativesObservation:
        observed_at = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        inst_id = symbol.upper().replace("/", "-").replace(":USDT", "-USDT")
        if not inst_id.endswith("-SWAP"):
            if not inst_id.endswith("-USDT"):
                inst_id = f"{inst_id}-USDT"
            inst_id = f"{inst_id}-SWAP"
        ticker = self._get_rows("/market/ticker", inst_id)
        mark = self._get_rows("/public/mark-price", inst_id)
        oi = self._get_rows("/public/open-interest", inst_id)
        funding = self._get_rows("/public/funding-rate", inst_id)
        if not ticker or not oi:
            raise ValueError(f"OKX perpetual not found: {inst_id}")
        ticker_row, oi_row = ticker[0], oi[0]
        mark_row = mark[0] if mark else {}
        funding_row = funding[0] if funding else {}
        exchange_timestamp = _timestamp_ms(ticker_row.get("ts"), observed_at)
        funding_timestamp = _timestamp_ms(funding_row.get("fundingTime"), exchange_timestamp) if funding_row.get("fundingTime") else exchange_timestamp
        values = {"mark_price": mark_row.get("markPx") or ticker_row.get("last"), "open_interest_usd": oi_row.get("oiUsd"), "funding_rate": funding_row.get("fundingRate")}
        missing = tuple(key for key, value in values.items() if value in (None, ""))
        return DerivativesObservation(
            venue=self.venue,
            symbol=_base_symbol(inst_id),
            market_type="perpetual",
            observed_at=observed_at,
            exchange_timestamp=exchange_timestamp,
            mark_price=values["mark_price"],
            index_price=ticker_row.get("idxPx"),
            open_interest=oi_row.get("oi"),
            open_interest_usd=values["open_interest_usd"],
            funding_rate=values["funding_rate"],
            funding_interval_hours=self._funding_interval_hours(funding_row.get("fundingInterval")),
            volume_24h_usd=ticker_row.get("volCcy24h") or ticker_row.get("vol24h"),
            instrument=inst_id,
            status="fresh" if not missing else "degraded",
            source="okx.v5.public",
            missing_fields=missing,
        )


OKXDerivativesAdapter = OkxDerivativesAdapter
