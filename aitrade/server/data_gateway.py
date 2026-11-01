from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from aitrade.dataflows.china_stock import (
    ChinaStockError,
    format_user_facing_error,
    normalize_cn_stock_symbol,
    search_cn_stocks,
)
from aitrade.server.a_share_collector import AShareCollector
from aitrade.server.cache_store import CacheStore
from aitrade.server.precache import remember_precache_symbol

security = HTTPBearer()


def _cache_dir() -> Path:
    configured = os.environ.get("AITRADE_DATA_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".aitrade" / "server_data_cache"


def _cache_ttl_seconds() -> int:
    raw = os.environ.get("AITRADE_DATA_CACHE_TTL_SECONDS", "86400")
    try:
        return max(0, int(raw))
    except ValueError:
        return 86400


def _cache_store() -> CacheStore:
    return CacheStore(_cache_dir())


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _records_from_dataframe(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    clean = df.where(pd.notna(df), None)
    return clean.to_dict(orient="records")


def _require_data_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict[str, Any]:
    from aitrade.server.auth_api import _verify_token

    return _verify_token(credentials.credentials)


def _fetch_price_dataframe(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    from aitrade.dataflows.china_stock import _configured_price_dataframe

    return _configured_price_dataframe(symbol, start_date, end_date)


def _fetch_stock_search(query: str, limit: int = 20) -> list[dict[str, str]]:
    return search_cn_stocks(query, limit=limit)


def _price_payload(symbol: str, start_date: str, end_date: str, source: str, df: pd.DataFrame) -> dict[str, Any]:
    records = _records_from_dataframe(df)
    return {
        "symbol": normalize_cn_stock_symbol(symbol),
        "start_date": start_date,
        "end_date": end_date,
        "source": source,
        "fetched_at": _now_iso(),
        "records": records,
        "data_status": "complete" if records else "missing",
        "message": "" if records else "公开数据暂时不足，系统会稍后自动重试。",
    }


def register_data_gateway_routes(app: FastAPI) -> None:
    @app.get("/data/stocks/search")
    def data_stock_search(
        q: str,
        limit: int = 20,
        _: dict[str, Any] = Depends(_require_data_user),
    ) -> dict[str, Any]:
        query = q.strip()
        if not query:
            return {"items": [], "source": "empty"}

        store = _cache_store()
        cached = store.read("search", [query, str(limit)], ttl_seconds=_cache_ttl_seconds())
        if cached is not None:
            cached["source"] = "cache"
            return cached

        try:
            payload = {"items": _fetch_stock_search(query, limit=limit), "source": "live", "fetched_at": _now_iso()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"股票搜索暂时不可用：{format_user_facing_error(exc)}") from exc
        store.write("search", [query, str(limit)], payload)
        return payload

    @app.get("/data/cn/prices")
    def data_cn_prices(
        symbol: str,
        start_date: str,
        end_date: str,
        _: dict[str, Any] = Depends(_require_data_user),
    ) -> dict[str, Any]:
        try:
            normalized = normalize_cn_stock_symbol(symbol)
        except ChinaStockError as exc:
            raise HTTPException(status_code=400, detail=format_user_facing_error(exc)) from exc

        store = _cache_store()
        cached = store.read("prices", [normalized, start_date, end_date], ttl_seconds=_cache_ttl_seconds())
        if cached is not None:
            cached["source"] = "cache"
            return cached
        remember_precache_symbol(normalized)

        try:
            df = _fetch_price_dataframe(normalized, start_date, end_date)
            payload = _price_payload(normalized, start_date, end_date, "live", df)
        except Exception:
            payload = {"data_status": "missing"}

        if payload["data_status"] == "missing":
            payload = AShareCollector(store).collect_prices(normalized, start_date, end_date)
            payload["source"] = "live"
        if payload["data_status"] == "complete":
            store.write("prices", [normalized, start_date, end_date], payload)
        return payload
