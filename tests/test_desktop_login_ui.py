from pathlib import Path


def test_login_screen_does_not_expose_auth_server_input():
    app_source = Path("src/App.tsx").read_text(encoding="utf-8")

    assert "授权服务器" not in app_source
    assert "auth.example.com" not in app_source
    assert "<Server" not in app_source


def test_authenticated_workspace_shows_license_expiry():
    app_source = Path("src/App.tsx").read_text(encoding="utf-8")

    assert "授权有效期" in app_source
    assert "licenseRemainingLabel" in app_source


def test_customer_workspace_hides_data_source_and_model_configuration():
    app_source = Path("src/App.tsx").read_text(encoding="utf-8")

    assert "服务器缓存" not in app_source
    assert "服务器公开数据缓存" not in app_source
    assert "服务器统一模型" not in app_source
    assert "API Key" not in app_source
    assert "接口地址" not in app_source
    assert "Tushare Token" not in app_source
    assert 'custom_api_key: apiKey.trim() ? apiKey.trim() : undefined' not in app_source


def test_progress_event_types_are_displayed_in_chinese():
    app_source = Path("src/App.tsx").read_text(encoding="utf-8")

    assert "eventTypeLabel" in app_source
    assert 'status: "状态"' in app_source
    assert 'completed: "完成"' in app_source
    assert 'error: "错误"' in app_source
    assert "eventTypeLabel(event.type)" in app_source


def test_workspace_explains_depth_and_exports_reports():
    app_source = Path("src/App.tsx").read_text(encoding="utf-8")

    assert "数字表示研究讨论和风险讨论轮数" in app_source
    assert "历史分析报告" in app_source
    assert "导出 PDF" in app_source
    assert "printReportAsPdf" in app_source
    assert "loadReportHistory" in app_source
    assert "检测到旧版本本地服务" in app_source
