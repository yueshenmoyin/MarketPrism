import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_server_state(monkeypatch, tmp_path):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    monkeypatch.setenv("AITRADE_AUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("AITRADE_DATA_CACHE_DIR", str(tmp_path / "data-cache"))


def _authorized_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/admin/api/users",
        auth=("admin", "admin-secret"),
        json={"username": "demo", "password": "secret", "expires_at": "2099-01-01"},
    )
    token = client.post("/auth/login", json={"username": "demo", "password": "secret"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
def test_server_data_gateway_requires_login():
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.get(
        "/data/cn/prices",
        params={"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"},
    )

    assert response.status_code in {401, 403}


@pytest.mark.unit
def test_server_data_gateway_caches_cn_price_data(monkeypatch):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app

    calls = {"count": 0}
    remembered = []

    def fake_fetch(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        calls["count"] += 1
        assert symbol == "600519.SH"
        assert start_date == "2026-05-18"
        assert end_date == "2026-05-23"
        return pd.DataFrame(
            [
                {
                    "Date": "2026-05-18",
                    "Open": 1680.1,
                    "High": 1700.0,
                    "Low": 1678.0,
                    "Close": 1695.2,
                    "Volume": 32100,
                }
            ]
        )

    monkeypatch.setattr(data_gateway, "_fetch_price_dataframe", fake_fetch)
    monkeypatch.setattr(data_gateway, "remember_precache_symbol", lambda symbol: remembered.append(symbol))

    client = TestClient(create_app())
    headers = _authorized_headers(client)
    params = {"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"}

    first = client.get("/data/cn/prices", params=params, headers=headers)
    second = client.get("/data/cn/prices", params=params, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1
    assert first.json()["source"] == "live"
    assert second.json()["source"] == "cache"
    assert second.json()["records"][0]["Close"] == 1695.2
    assert remembered == ["600519.SH"]


@pytest.mark.unit
def test_server_data_gateway_search_uses_cache(monkeypatch):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app

    calls = {"count": 0}

    def fake_search(query: str, limit: int = 20):
        calls["count"] += 1
        return [{"symbol": "600519.SH", "code": "600519", "name": "贵州茅台", "market": "上海证券交易所"}]

    monkeypatch.setattr(data_gateway, "_fetch_stock_search", fake_search)

    client = TestClient(create_app())
    headers = _authorized_headers(client)

    first = client.get("/data/stocks/search", params={"q": "茅台"}, headers=headers)
    second = client.get("/data/stocks/search", params={"q": "茅台"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["count"] == 1
    assert second.json()["source"] == "cache"
    assert second.json()["items"][0]["symbol"] == "600519.SH"


@pytest.mark.unit
def test_server_data_gateway_returns_chinese_partial_payload_when_public_data_missing(monkeypatch):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app

    def fake_fetch(symbol: str, start_date: str, end_date: str):
        return pd.DataFrame()

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
                "message": "公开数据暂时不足，系统会稍后自动重试。",
            }

    monkeypatch.setattr(data_gateway, "_fetch_price_dataframe", fake_fetch)
    monkeypatch.setattr(data_gateway, "AShareCollector", EmptyCollector)

    client = TestClient(create_app())
    headers = _authorized_headers(client)
    response = client.get(
        "/data/cn/prices",
        params={"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    assert payload["data_status"] == "missing"
    assert "公开数据" in payload["message"]


@pytest.mark.unit
def test_server_data_gateway_returns_partial_payload_when_public_fetch_raises(monkeypatch):
    from aitrade.server import data_gateway
    from aitrade.server.auth_api import create_app

    def fake_fetch(symbol: str, start_date: str, end_date: str):
        raise RuntimeError("RemoteDisconnected")

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
                "message": "公开数据暂时不足，系统会稍后自动重试。",
            }

    monkeypatch.setattr(data_gateway, "_fetch_price_dataframe", fake_fetch)
    monkeypatch.setattr(data_gateway, "AShareCollector", EmptyCollector)

    client = TestClient(create_app())
    headers = _authorized_headers(client)
    response = client.get(
        "/data/cn/prices",
        params={"symbol": "600519.SH", "start_date": "2026-05-18", "end_date": "2026-05-23"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["records"] == []
    assert payload["data_status"] == "missing"
    assert "RemoteDisconnected" not in payload["message"]
