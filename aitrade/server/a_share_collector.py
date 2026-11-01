from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from aitrade.dataflows.china_stock import (
    ADataChinaStockProvider,
    AkShareChinaStockProvider,
    BaoStockChinaStockProvider,
    FinShareChinaStockProvider,
    normalize_cn_stock_symbol,
)
from aitrade.server.cache_store import CacheStore


class AShareCollector:
    def __init__(self, cache_store: CacheStore, providers: list[Any] | None = None):
        self.cache_store = cache_store
        self.providers = providers or [
            AkShareChinaStockProvider(),
            BaoStockChinaStockProvider(),
            FinShareChinaStockProvider(),
            ADataChinaStockProvider(),
        ]

    def collect_prices(self, symbol: str, start_date: str, end_date: str) -> dict[str, Any]:
        normalized = normalize_cn_stock_symbol(symbol)
        for provider in self.providers:
            try:
                df = provider.get_price_dataframe(normalized, start_date, end_date)
            except Exception:
                continue
            payload = self._payload(normalized, start_date, end_date, df)
            if payload["data_status"] == "complete":
                self.cache_store.write("prices", [normalized, start_date, end_date], payload)
            return payload

        payload = self._payload(normalized, start_date, end_date, pd.DataFrame())
        return payload

    def _payload(self, symbol: str, start_date: str, end_date: str, df: pd.DataFrame) -> dict[str, Any]:
        records = [] if df.empty else df.where(pd.notna(df), None).to_dict(orient="records")
        return {
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "records": records,
            "data_status": "complete" if records else "missing",
            "message": "" if records else "公开数据暂时不足，系统会稍后自动重试。",
            "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
