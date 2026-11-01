import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AITRADE_AUTH_USERNAME", "admin")
    monkeypatch.setenv("AITRADE_AUTH_PASSWORD", "admin-secret")
    monkeypatch.setenv("AITRADE_AUTH_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("AITRADE_RESULTS_DIR", str(tmp_path / "reports"))


def _login_headers(client):
    client.post(
        "/admin/api/users",
        auth=("admin", "admin-secret"),
        json={"username": "demo", "password": "secret", "expires_at": "2099-01-01"},
    )
    token = client.post("/auth/login", json={"username": "demo", "password": "secret"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
def test_server_analysis_job_returns_task_id(monkeypatch):
    from aitrade.server import analysis_jobs
    from aitrade.server.auth_api import create_app

    def fake_run_job(self, task, request):
        task.status = "completed"
        task.report = "# 600519.SH 股票分析报告\n\n模拟报告"
        task.report_id = "mock.md"
        task.push({"type": "completed", "message": "分析完成。", "report_id": "mock.md"})
        task.finish()

    monkeypatch.setattr(analysis_jobs.AnalysisJobManager, "_run_job", fake_run_job)

    client = TestClient(create_app())
    headers = _login_headers(client)
    response = client.post(
        "/analysis/start",
        headers=headers,
        json={"ticker": "600519.SH", "analysis_date": "2026-05-23", "analysts": ["market"], "research_depth": 1},
    )

    assert response.status_code == 200
    assert response.json()["analysis_id"]


@pytest.mark.unit
def test_server_analysis_job_requires_login():
    from aitrade.server.auth_api import create_app

    client = TestClient(create_app())
    response = client.post("/analysis/start", json={"ticker": "600519.SH"})

    assert response.status_code in {401, 403}
