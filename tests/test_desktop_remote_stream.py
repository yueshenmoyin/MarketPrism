import pytest


@pytest.mark.unit
def test_remote_event_stream_uses_no_read_timeout(monkeypatch):
    from aitrade.dataflows.config import set_config
    from aitrade.desktop import api as desktop_api

    set_config(
        {
            "auth_server_url": "https://api.example.com",
            "auth_token": "token",
            "server_analysis_enabled": True,
        }
    )
    calls = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self, decode_unicode=True):
            yield "data: {\"type\":\"status\",\"message\":\"ok\"}"
            yield ""

    def fake_get(url, headers, timeout, stream=False):
        calls.append((url, headers, timeout, stream))
        return Response()

    monkeypatch.setattr(desktop_api.requests, "get", fake_get)

    iterator = desktop_api._remote_event_stream("/analysis/task-1/events")

    assert iterator is not None
    assert list(iterator) == ['data: {"type":"status","message":"ok"}\n', "\n"]
    assert calls == [
        (
            "https://api.example.com/analysis/task-1/events",
            {"Authorization": "Bearer token"},
            (10, None),
            True,
        )
    ]
