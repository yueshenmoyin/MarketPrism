import pandas as pd
import pytest


@pytest.mark.unit
def test_collector_caches_price_data_from_first_available_provider(tmp_path):
    from aitrade.server.a_share_collector import AShareCollector
    from aitrade.server.cache_store import CacheStore

    calls = []

    class FailingProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            calls.append("fail")
            raise RuntimeError("temporary failure")

    class WorkingProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            calls.append("work")
            return pd.DataFrame([{"Date": "2026-05-18", "Close": 1695.2}])

    collector = AShareCollector(CacheStore(tmp_path), providers=[FailingProvider(), WorkingProvider()])
    payload = collector.collect_prices("600519.SH", "2026-05-18", "2026-05-23")

    assert calls == ["fail", "work"]
    assert payload["records"][0]["Close"] == 1695.2
    assert payload["data_status"] == "complete"
    cached = CacheStore(tmp_path).read("prices", ["600519.SH", "2026-05-18", "2026-05-23"], ttl_seconds=86400)
    assert cached["records"][0]["Close"] == 1695.2


@pytest.mark.unit
def test_collector_returns_partial_status_for_empty_data(tmp_path):
    from aitrade.server.a_share_collector import AShareCollector
    from aitrade.server.cache_store import CacheStore

    class EmptyProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            return pd.DataFrame()

    collector = AShareCollector(CacheStore(tmp_path), providers=[EmptyProvider()])
    payload = collector.collect_prices("600519.SH", "2026-05-18", "2026-05-23")

    assert payload["records"] == []
    assert payload["data_status"] == "missing"
    assert "公开数据暂时不足" in payload["message"]
