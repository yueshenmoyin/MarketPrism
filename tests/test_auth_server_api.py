import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_user_store(monkeypatch, tmp_path):
    monkeypatch.setenv("AITRADE_AUTH_USERS_FILE", str(tmp_path / "users.json"))


@pytest.mark.unit
def test_auth_server_login_returns_token_for_configured_user(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "demo")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "demo", "password": "secret"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["user"] == {"name": "demo", "username": "demo"}
    assert "password" not in payload


@pytest.mark.unit
def test_auth_server_verifies_env_admin_token(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "demo")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    token = client.post("/auth/login", json={"username": "demo", "password": "secret"}).json()["access_token"]

    verify_response = client.post("/auth/verify", json={"token": token})

    assert verify_response.status_code == 200
    assert verify_response.json()["user"] == {"name": "demo", "username": "demo"}


@pytest.mark.unit
def test_auth_server_rejects_invalid_credentials(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "demo")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "demo", "password": "wrong"})

    assert response.status_code == 401
    assert "账号或密码" in response.json()["detail"]


@pytest.mark.unit
def test_auth_server_requires_password_configuration(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "demo")
    monkeypatch.delenv("AITRADE_AUTH_PASSWORD", raising=False)
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "demo", "password": "secret"})

    assert response.status_code == 503
    assert "未配置" in response.json()["detail"]


@pytest.mark.unit
def test_admin_api_creates_user_and_login_returns_license_expiry(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    admin_auth = ("admin", "admin-secret")
    expires_at = "2099-01-01T00:00:00Z"

    create_response = client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "demo", "password": "user-secret", "expires_at": expires_at},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["username"] == "demo"
    assert created["expires_at"] == expires_at
    assert created["enabled"] is True
    assert "password" not in created
    assert "password_hash" not in created

    list_response = client.get("/admin/api/users", auth=admin_auth)
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["username"] == "demo"

    login_response = client.post("/auth/login", json={"username": "demo", "password": "user-secret"})
    assert login_response.status_code == 200
    payload = login_response.json()
    assert payload["user"]["username"] == "demo"
    assert payload["expires_at"] == expires_at
    assert payload["expires_in_seconds"] > 0


@pytest.mark.unit
def test_auth_server_verifies_issued_user_token_and_rejects_after_disable(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    admin_auth = ("admin", "admin-secret")
    expires_at = "2099-01-01T00:00:00Z"
    client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "demo", "password": "user-secret", "expires_at": expires_at},
    )
    login_response = client.post("/auth/login", json={"username": "demo", "password": "user-secret"})
    token = login_response.json()["access_token"]

    verify_response = client.post("/auth/verify", json={"token": token})

    assert verify_response.status_code == 200
    assert verify_response.json()["user"] == {"name": "demo", "username": "demo"}
    assert verify_response.json()["expires_at"] == expires_at

    client.patch("/admin/api/users/demo", auth=admin_auth, json={"enabled": False})
    disabled_response = client.post("/auth/verify", json={"token": token})

    assert disabled_response.status_code == 403
    assert "已停用" in disabled_response.json()["detail"]


@pytest.mark.unit
def test_auth_server_password_update_invalidates_existing_tokens(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    admin_auth = ("admin", "admin-secret")
    client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "demo", "password": "old-secret", "expires_at": "2099-01-01T00:00:00Z"},
    )
    token = client.post("/auth/login", json={"username": "demo", "password": "old-secret"}).json()["access_token"]

    client.patch("/admin/api/users/demo", auth=admin_auth, json={"password": "new-secret"})
    verify_response = client.post("/auth/verify", json={"token": token})

    assert verify_response.status_code == 401
    assert "登录已失效" in verify_response.json()["detail"]


@pytest.mark.unit
def test_admin_api_updates_password_and_expiry(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    admin_auth = ("admin", "admin-secret")
    client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "demo", "password": "old-secret", "expires_at": "2099-01-01T00:00:00Z"},
    )

    update_response = client.patch(
        "/admin/api/users/demo",
        auth=admin_auth,
        json={"password": "new-secret", "expires_at": "2099-02-01T00:00:00Z", "enabled": True},
    )

    assert update_response.status_code == 200
    assert update_response.json()["expires_at"] == "2099-02-01T00:00:00Z"
    old_login = client.post("/auth/login", json={"username": "demo", "password": "old-secret"})
    new_login = client.post("/auth/login", json={"username": "demo", "password": "new-secret"})
    assert old_login.status_code == 401
    assert new_login.status_code == 200


@pytest.mark.unit
def test_auth_server_rejects_expired_or_disabled_users(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    admin_auth = ("admin", "admin-secret")
    client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "expired", "password": "secret", "expires_at": "2020-01-01T00:00:00Z"},
    )
    client.post(
        "/admin/api/users",
        auth=admin_auth,
        json={"username": "disabled", "password": "secret", "expires_at": "2099-01-01T00:00:00Z"},
    )
    client.patch("/admin/api/users/disabled", auth=admin_auth, json={"enabled": False})

    expired_response = client.post("/auth/login", json={"username": "expired", "password": "secret"})
    disabled_response = client.post("/auth/login", json={"username": "disabled", "password": "secret"})

    assert expired_response.status_code == 403
    assert "授权已过期" in expired_response.json()["detail"]
    assert disabled_response.status_code == 403
    assert "已停用" in disabled_response.json()["detail"]


@pytest.mark.unit
def test_admin_page_requires_admin_auth_and_returns_html(monkeypatch):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())

    unauthenticated = client.get("/admin")
    authenticated = client.get("/admin", auth=("admin", "admin-secret"))

    assert unauthenticated.status_code == 401
    assert authenticated.status_code == 200
    assert "text/html" in authenticated.headers["content-type"]
    assert "MarketPrism 用户管理" in authenticated.text
