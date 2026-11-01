import pandas as pd
import pytest


@pytest.mark.unit
def test_collector_does_not_cache_missing_price_data(tmp_path):
    from aitrade.server.a_share_collector import AShareCollector
    from aitrade.server.cache_store import CacheStore

    class EmptyProvider:
        def get_price_dataframe(self, symbol, start_date, end_date):
            return pd.DataFrame()

    store = CacheStore(tmp_path)
    collector = AShareCollector(store, providers=[EmptyProvider()])

    payload = collector.collect_prices("600519.SH", "2026-05-18", "2026-05-23")

    assert payload["data_status"] == "missing"
    assert store.read("prices", ["600519.SH", "2026-05-18", "2026-05-23"], ttl_seconds=86400) is None


@pytest.mark.unit
def test_gateway_does_not_reuse_missing_price_payload(monkeypatch, tmp_path):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    monkeypatch.setenv("AITRADE_AUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("AITRADE_DATA_CACHE_DIR", str(tmp_path / "data-cache"))

    calls = {"count": 0}

    def fake_fetch(symbol: str, start_date: str, end_date: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return pd.DataFrame()
        return pd.DataFrame([{"Date": "2026-05-18", "Close": 1695.2}])

    class EmptyCollector:
        def __init__(self, store):
            self.store = store

        def collect_prices(self, symbol: str, start_date: str, end_date: str):
            return {
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "records": [],
                "data_status": "missing",
                "message": "public data is temporarily unavailable",
            }

    monkeypatch.setattr(data_gateway, "_fetch_price_dataframe", fake_fetch)
    monkeypatch.setattr(data_gateway, "AShareCollector", EmptyCollector)

    client = TestClient(create_app())
    client.post(
        "/admin/api/users",
        auth=("admin", "admin-secret"),
        json={"username": "demo", "password": "secret", "expires_at": "2099-01-01"},
    )
    token = client.post("/auth/login", json={"username": "demo", "password": "secret"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    params = {"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"}

    first = client.get("/data/cn/prices", params=params, headers=headers)
    second = client.get("/data/cn/prices", params=params, headers=headers)

    assert first.status_code == 200
    assert first.json()["data_status"] == "missing"
    assert second.status_code == 200
    assert second.json()["data_status"] == "complete"
    assert calls["count"] == 2
