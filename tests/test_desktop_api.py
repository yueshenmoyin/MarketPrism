import json
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _desktop_config_path(monkeypatch, tmp_path):
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(tmp_path / "desktop_config.json"))


@pytest.mark.unit
def test_desktop_api_returns_chinese_default_config():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.get("/config")

    assert response.status_code == 200
    payload = response.json()
    assert payload["primary_market"] == "cn_a_share"
    assert payload["output_language"] == "Simplified Chinese"
    assert payload["data_vendors"]["cn_stock"] == "akshare"
    assert payload["advanced_mode"] is False


@pytest.mark.unit
def test_desktop_api_exposes_healthcheck():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "backend": "desktop", "version": "0.1.0"}


@pytest.mark.unit
def test_desktop_report_localizes_structured_decision_labels():
    from aitrade.desktop.api import _render_report

    report = _render_report(
        "600519.SH",
        "2026-05-24",
        {
            "final_trade_decision": (
                "**Rating**: Hold\n\n"
                "**Executive Summary**: 当前维持观察。\n\n"
                "**Investment Thesis**: 基本面稳健。\n\n"
                "**Price Target**: 1400.0\n\n"
                "**Time Horizon**: 3个月"
            )
        },
        "Hold",
    )

    assert "- 最终评级: 持有" in report
    assert "**评级**：持有" in report
    assert "**执行摘要**：当前维持观察。" in report
    assert "**投资逻辑**：基本面稳健。" in report
    assert "**目标价**：1400.0" in report
    assert "**投资周期**：3个月" in report
    assert "Executive Summary" not in report


@pytest.mark.unit
def test_desktop_api_saves_lists_and_reads_report_history(tmp_path):
    from aitrade.desktop import api as desktop_api
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    config_response = client.post("/config", json={"results_dir": str(tmp_path / "reports")})
    assert config_response.status_code == 200

    saved_path = desktop_api._save_report_file(
        ticker="600519.SH",
        analysis_date="2026-05-24",
        report="# 600519.SH 股票分析报告\n\n正文内容",
    )

    list_response = client.get("/reports")
    assert list_response.status_code == 200
    items = list_response.json()["items"]
    assert items[0]["id"] == saved_path.name
    assert items[0]["ticker"] == "600519.SH"
    assert items[0]["analysis_date"] == "2026-05-24"
    assert items[0]["title"] == "600519.SH 股票分析报告"

    read_response = client.get(f"/reports/{saved_path.name}")
    assert read_response.status_code == 200
    payload = read_response.json()
    assert payload["id"] == saved_path.name
    assert payload["report"].startswith("# 600519.SH 股票分析报告")


@pytest.mark.unit
def test_desktop_api_falls_back_to_local_report_history_when_server_reports_are_missing(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api
    from aitrade.desktop.api import create_app

    config_path = tmp_path / "desktop_config.json"
    reports_dir = tmp_path / "reports"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://api.example.com",
                "auth_token": "token",
                "auth_user": {"username": "demo"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
                "results_dir": str(reports_dir),
                "server_analysis_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))
    monkeypatch.setattr(
        desktop_api,
        "_post_auth_verify",
        lambda auth_server_url, token: {
            "access_token": token,
            "user": {"username": "demo"},
            "expires_at": "2099-01-01T00:00:00Z",
        },
        raising=False,
    )

    class Response:
        status_code = 404
        text = '{"detail":"Not Found"}'

        def json(self):
            return {"detail": "Not Found"}

    calls = []

    def fake_get(url, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(desktop_api.requests, "get", fake_get)

    client = TestClient(create_app())
    saved_path = desktop_api._save_report_file(
        ticker="600519.SH",
        analysis_date="2026-05-24",
        report="# 600519.SH 股票分析报告\n\n正文内容",
    )

    response = client.get("/reports")

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == saved_path.name
    assert calls == [("https://api.example.com/reports", {"Authorization": "Bearer token"}, 30)]


@pytest.mark.unit
def test_desktop_api_localizes_old_saved_report_on_read(tmp_path):
    from aitrade.desktop import api as desktop_api
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    config_response = client.post("/config", json={"results_dir": str(tmp_path / "reports")})
    assert config_response.status_code == 200

    saved_path = desktop_api._save_report_file(
        ticker="600519.SH",
        analysis_date="2026-05-24",
        report=(
            "# 600519.SH 股票分析报告\n\n"
            "## 组合经理决策\n\n"
            "**Rating**: Hold\n\n"
            "**Executive Summary**: 维持观察。\n\n"
            "**Investment Thesis**: 基本面稳健。\n\n"
            "**Price Target**: 1362.0\n\n"
            "**Time Horizon**: 1-3个月"
        ),
    )

    payload = client.get(f"/reports/{saved_path.name}").json()

    assert "**评级**：持有" in payload["report"]
    assert "**执行摘要**：维持观察。" in payload["report"]
    assert "Executive Summary" not in payload["report"]


@pytest.mark.unit
def test_desktop_api_allows_tauri_production_origin():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.options(
        "/config",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


@pytest.mark.unit
def test_desktop_api_updates_custom_gateway_config():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/config",
        json={
            "llm_provider": "custom_openai_compatible",
            "custom_base_url": "https://gateway.example.com/v1",
            "custom_api_key": "secret",
            "quick_think_llm": "qwen-fast",
            "deep_think_llm": "qwen-pro",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_provider"] == "custom_openai_compatible"
    assert payload["backend_url"] == "https://gateway.example.com/v1"
    assert payload["quick_think_llm"] == "qwen-fast"
    assert payload["deep_think_llm"] == "qwen-pro"
    assert payload["custom_api_key"] == "***"


@pytest.mark.unit
def test_desktop_api_login_requires_auth_server_url():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.post("/auth/login", json={"username": "demo", "password": "secret"})

    assert response.status_code == 400
    assert "联系管理员" in response.json()["detail"]


@pytest.mark.unit
def test_auth_server_english_error_detail_is_localized():
    from aitrade.desktop.api import _extract_error_detail

    class Response:
        text = ""

        def json(self):
            return {"detail": "invalid credentials"}

    assert _extract_error_detail(Response()) == "账号或密码不正确"


@pytest.mark.unit
def test_desktop_api_login_proxies_server_and_persists_masked_session(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))
    calls = []

    def fake_post_auth_login(auth_server_url, username, password):
        calls.append((auth_server_url, username, password))
        return {
            "access_token": "server-token",
            "user": {"name": "测试用户", "account": username},
            "expires_at": "2099-01-01T00:00:00Z",
            "expires_in_seconds": 2300000000,
        }

    def fake_post_auth_verify(auth_server_url, token):
        return {
            "access_token": token,
            "user": {"name": "测试用户", "account": "demo"},
            "expires_at": "2099-01-01T00:00:00Z",
            "expires_in_seconds": 2300000000,
        }

    monkeypatch.setattr(desktop_api, "_post_auth_login", fake_post_auth_login, raising=False)
    monkeypatch.setattr(desktop_api, "_post_auth_verify", fake_post_auth_verify, raising=False)

    client = TestClient(desktop_api.create_app())
    response = client.post(
        "/auth/login",
        json={
            "auth_server_url": "https://auth.example.com/",
            "username": "demo",
            "password": "secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "authenticated": True,
        "auth_server_url": "https://auth.example.com",
        "user": {"name": "测试用户", "account": "demo"},
        "expires_at": "2099-01-01T00:00:00Z",
        "expires_in_seconds": payload["expires_in_seconds"],
    }
    assert payload["expires_in_seconds"] > 0
    assert calls == [("https://auth.example.com", "demo", "secret")]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth_server_url"] == "https://auth.example.com"
    assert saved["auth_token"] == "server-token"
    assert saved["auth_user"] == {"name": "测试用户", "account": "demo"}
    assert saved["auth_expires_at"] == "2099-01-01T00:00:00Z"
    assert "password" not in saved

    session = client.get("/auth/session")
    assert session.status_code == 200
    assert session.json()["authenticated"] is True
    assert session.json()["expires_at"] == "2099-01-01T00:00:00Z"

    config_response = client.get("/config")
    assert config_response.status_code == 200
    assert config_response.json()["auth_token"] == "***"


@pytest.mark.unit
def test_desktop_api_verifies_saved_session_with_auth_server(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://auth.example.com",
                "auth_token": "saved-token",
                "auth_user": {"name": "旧用户"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))
    calls = []

    def fake_post_auth_verify(auth_server_url, token):
        calls.append((auth_server_url, token))
        return {
            "access_token": token,
            "user": {"name": "测试用户", "username": "demo"},
            "expires_at": "2099-02-01T00:00:00Z",
        }

    monkeypatch.setattr(desktop_api, "_post_auth_verify", fake_post_auth_verify, raising=False)

    client = TestClient(desktop_api.create_app())
    response = client.get("/auth/session")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["user"] == {"name": "测试用户", "username": "demo"}
    assert response.json()["expires_at"] == "2099-02-01T00:00:00Z"
    assert calls == [("https://auth.example.com", "saved-token")]

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth_user"] == {"name": "测试用户", "username": "demo"}
    assert saved["auth_expires_at"] == "2099-02-01T00:00:00Z"


@pytest.mark.unit
def test_desktop_api_clears_saved_session_when_server_rejects_token(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://auth.example.com",
                "auth_token": "stale-token",
                "auth_user": {"name": "旧用户"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))

    def fake_post_auth_verify(auth_server_url, token):
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")

    monkeypatch.setattr(desktop_api, "_post_auth_verify", fake_post_auth_verify, raising=False)

    client = TestClient(desktop_api.create_app())
    response = client.get("/auth/session")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth_token"] is None
    assert saved["auth_user"] is None
    assert saved["auth_expires_at"] is None


@pytest.mark.unit
def test_desktop_api_revalidates_session_before_starting_analysis(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://auth.example.com",
                "auth_token": "disabled-token",
                "auth_user": {"name": "旧用户"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))

    def fake_post_auth_verify(auth_server_url, token):
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员。")

    monkeypatch.setattr(desktop_api, "_post_auth_verify", fake_post_auth_verify, raising=False)

    client = TestClient(desktop_api.create_app())
    response = client.post("/analysis/start", json={"ticker": "600519.SH"})

    assert response.status_code == 401
    assert "请先登录" in response.json()["detail"]


@pytest.mark.unit
def test_desktop_api_logout_clears_saved_session(monkeypatch, tmp_path):
    from aitrade.desktop.api import create_app

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://auth.example.com",
                "auth_token": "old-token",
                "auth_user": {"name": "测试用户"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))

    client = TestClient(create_app())
    response = client.post("/auth/logout")

    assert response.status_code == 200
    assert response.json()["authenticated"] is False

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["auth_token"] is None
    assert saved["auth_user"] is None
    assert saved["auth_expires_at"] is None
    assert saved["auth_server_url"] == "https://auth.example.com"


@pytest.mark.unit
def test_desktop_api_persists_config_across_app_restart(monkeypatch, tmp_path):
    from aitrade.desktop.api import create_app

    first_client = TestClient(create_app())
    response = first_client.post(
        "/config",
        json={
            "llm_provider": "custom_openai_compatible",
            "custom_base_url": "https://gateway.example.com/v1",
            "custom_api_key": "persisted-secret",
            "quick_think_llm": "qwen-fast",
            "deep_think_llm": "qwen-pro",
            "cn_stock_vendor": "tushare",
            "results_dir": str(tmp_path / "reports"),
        },
    )
    assert response.status_code == 200

    second_client = TestClient(create_app())
    persisted = second_client.get("/config").json()

    assert persisted["llm_provider"] == "custom_openai_compatible"
    assert persisted["backend_url"] == "https://gateway.example.com/v1"
    assert persisted["custom_api_key"] == "***"
    assert persisted["quick_think_llm"] == "qwen-fast"
    assert persisted["deep_think_llm"] == "qwen-pro"
    assert persisted["data_vendors"]["cn_stock"] == "tushare"
    assert persisted["results_dir"] == str(tmp_path / "reports")


@pytest.mark.unit
def test_desktop_api_accepts_baostock_as_cn_stock_vendor(tmp_path):
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.post(
        "/config",
        json={
            "cn_stock_vendor": "baostock",
            "results_dir": str(tmp_path / "reports"),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_vendors"]["cn_stock"] == "baostock"


@pytest.mark.unit
def test_desktop_api_persists_tushare_token_locally_and_masks_response(monkeypatch, tmp_path):
    from aitrade.desktop.api import create_app

    config_path = tmp_path / "desktop_config.json"
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    client = TestClient(create_app())
    response = client.post(
        "/config",
        json={
            "cn_stock_vendor": "tushare",
            "tushare_token": "ts-secret",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_vendors"]["cn_stock"] == "tushare"
    assert payload["tushare_token"] == "***"
    assert os.environ["TUSHARE_TOKEN"] == "ts-secret"

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["tushare_token"] == "ts-secret"

    config_response = client.get("/config")
    assert config_response.status_code == 200
    assert config_response.json()["tushare_token"] == "***"


@pytest.mark.unit
def test_desktop_api_restores_tushare_token_to_environment_on_restart(monkeypatch, tmp_path):
    from aitrade.desktop.api import create_app

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(json.dumps({"tushare_token": "persisted-ts-token"}), encoding="utf-8")
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)

    client = TestClient(create_app())
    response = client.get("/config")

    assert response.status_code == 200
    assert response.json()["tushare_token"] == "***"
    assert os.environ["TUSHARE_TOKEN"] == "persisted-ts-token"


@pytest.mark.unit
def test_desktop_api_exposes_stock_search(monkeypatch):
    from aitrade.desktop.api import create_app

    def fake_search(query, vendor_name=None):
        assert query == "茅台"
        assert vendor_name is None
        return [{"symbol": "600519.SH", "code": "600519", "name": "贵州茅台", "market": "上海证券交易所"}]

    monkeypatch.setattr("aitrade.desktop.api.search_cn_stocks", fake_search)

    client = TestClient(create_app())
    response = client.get("/stocks/search", params={"q": "茅台"})

    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "600519.SH"


@pytest.mark.unit
def test_desktop_api_stock_search_can_use_selected_cn_vendor(monkeypatch):
    from aitrade.desktop.api import create_app

    calls = []

    def fake_search(query, vendor_name=None):
        calls.append((query, vendor_name))
        return [{"symbol": "600519.SH", "code": "600519", "name": "贵州茅台", "market": "上海证券交易所"}]

    monkeypatch.setattr("aitrade.desktop.api.search_cn_stocks", fake_search)

    client = TestClient(create_app())
    response = client.get("/stocks/search", params={"q": "茅台", "market": "cn_a_share", "vendor": "baostock"})

    assert response.status_code == 200
    assert calls == [("茅台", "baostock")]


@pytest.mark.unit
def test_desktop_api_stock_search_localizes_provider_errors(monkeypatch):
    from aitrade.desktop.api import create_app

    def fake_search(query, vendor_name=None):
        raise RuntimeError(r"No such file or directory: D:\Programs\AITrade A-Share\aitrade-backend\_internal\akshare\file_fold\calendar.json")

    monkeypatch.setattr("aitrade.desktop.api.search_cn_stocks", fake_search)

    client = TestClient(create_app())
    response = client.get("/stocks/search", params={"q": "茅台"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "股票搜索失败" in detail
    assert "缺少必要的数据文件" in detail
    assert "No such file" not in detail
    assert "calendar.json" not in detail


@pytest.mark.unit
def test_desktop_api_exposes_us_and_hk_stock_entrypoints():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())

    us_response = client.get("/stocks/search", params={"q": "aapl", "market": "us_stock"})
    hk_response = client.get("/stocks/search", params={"q": "0700", "market": "hk_stock"})

    assert us_response.status_code == 200
    assert us_response.json()["items"][0] == {
        "symbol": "AAPL",
        "code": "AAPL",
        "name": "AAPL",
        "market": "美股",
    }
    assert hk_response.status_code == 200
    assert hk_response.json()["items"][0] == {
        "symbol": "0700.HK",
        "code": "0700",
        "name": "0700.HK",
        "market": "港股",
    }


@pytest.mark.unit
def test_desktop_api_switches_non_cn_markets_to_yfinance_vendors():
    from aitrade.desktop.api import create_app

    client = TestClient(create_app())
    response = client.post("/config", json={"primary_market": "hk_stock"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["primary_market"] == "hk_stock"
    assert payload["data_vendors"]["core_stock_apis"] == "yfinance"
    assert payload["data_vendors"]["technical_indicators"] == "yfinance"
    assert payload["data_vendors"]["fundamental_data"] == "yfinance"
    assert payload["data_vendors"]["news_data"] == "yfinance"


@pytest.mark.unit
def test_analysis_task_replays_events_for_reconnected_clients():
    from aitrade.desktop.api import AnalysisTask

    task = AnalysisTask("task-1")
    task.push({"type": "status", "message": "started"})
    task.push({"type": "completed", "message": "done"})
    task.finish()

    assert list(task.iter_events()) == [
        {"type": "status", "message": "started"},
        {"type": "completed", "message": "done"},
    ]
    assert list(task.iter_events()) == [
        {"type": "status", "message": "started"},
        {"type": "completed", "message": "done"},
    ]


@pytest.mark.unit
def test_analysis_manager_rejects_second_active_task(monkeypatch):
    from aitrade.desktop.api import AnalysisManager, AnalysisStartRequest

    def hold_task(self, task, request):
        task.status = "running"

    monkeypatch.setattr(AnalysisManager, "_run_analysis", hold_task)

    manager = AnalysisManager()
    manager.start(AnalysisStartRequest(ticker="600519.SH"))

    with pytest.raises(HTTPException) as caught:
        manager.start(AnalysisStartRequest(ticker="000001.SZ"))

    assert caught.value.status_code == 409


@pytest.mark.unit
def test_desktop_api_proxies_analysis_start_to_server(monkeypatch, tmp_path):
    from aitrade.desktop import api as desktop_api

    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(
            {
                "auth_server_url": "https://api.example.com",
                "auth_token": "token",
                "auth_user": {"username": "demo"},
                "auth_expires_at": "2099-01-01T00:00:00Z",
                "server_analysis_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AITRADE_DESKTOP_CONFIG", str(config_path))

    monkeypatch.setattr(
        desktop_api,
        "_post_auth_verify",
        lambda auth_server_url, token: {
            "access_token": token,
            "user": {"username": "demo"},
            "expires_at": "2099-01-01T00:00:00Z",
        },
        raising=False,
    )

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"analysis_id": "remote-1", "status": "running"}

    calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return Response()

    monkeypatch.setattr(desktop_api.requests, "post", fake_post)

    client = TestClient(desktop_api.create_app())
    response = client.post("/analysis/start", json={"ticker": "600519.SH", "analysts": ["market"], "research_depth": 1})

    assert response.status_code == 200
    assert response.json()["analysis_id"] == "remote-1"
    assert response.json()["mode"] == "remote"
    assert calls[0][0] == "https://api.example.com/analysis/start"
    assert calls[0][2] == {"Authorization": "Bearer token"}
