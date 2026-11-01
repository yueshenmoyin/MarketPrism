import os
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.unit
def test_cache_store_round_trips_json_payload(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    store.write("prices", ["600519.SH", "2026-05-01"], {"records": [{"Close": 1695.2}]})

    assert store.read("prices", ["600519.SH", "2026-05-01"], ttl_seconds=86400) == {
        "records": [{"Close": 1695.2}]
    }


@pytest.mark.unit
def test_cache_store_returns_none_for_expired_payload(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    path = store.write("prices", ["600519.SH"], {"records": []})
    old_time = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (old_time, old_time))

    assert store.read("prices", ["600519.SH"], ttl_seconds=60) is None


@pytest.mark.unit
def test_cache_store_keeps_different_chinese_queries_separate(tmp_path):
    from aitrade.server.cache_store import CacheStore

    store = CacheStore(tmp_path)
    store.write("search", ["茅台"], {"items": [{"symbol": "600519.SH"}]})
    store.write("search", ["平安"], {"items": [{"symbol": "000001.SZ"}]})

    assert store.read("search", ["茅台"], ttl_seconds=86400)["items"][0]["symbol"] == "600519.SH"
    assert store.read("search", ["平安"], ttl_seconds=86400)["items"][0]["symbol"] == "000001.SZ"
