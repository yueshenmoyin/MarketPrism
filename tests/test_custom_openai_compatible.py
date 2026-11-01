import pytest
from copy import deepcopy


@pytest.mark.unit
def test_custom_openai_compatible_provider_is_supported():
    from aitrade.llm_clients.factory import create_llm_client
    from aitrade.llm_clients.openai_client import OpenAIClient

    client = create_llm_client(
        "custom_openai_compatible",
        "custom-model",
        base_url="https://llm-gateway.example.com/v1",
        api_key="test-key",
    )

    assert isinstance(client, OpenAIClient)
    assert client.provider == "custom_openai_compatible"
    assert client.base_url == "https://llm-gateway.example.com/v1"
    assert client.kwargs["api_key"] == "test-key"


@pytest.mark.unit
def test_custom_openai_compatible_has_custom_model_options():
    from aitrade.llm_clients.model_catalog import get_model_options

    assert get_model_options("custom_openai_compatible", "quick") == [
        ("自定义模型 ID", "custom")
    ]
    assert get_model_options("custom_openai_compatible", "deep") == [
        ("自定义模型 ID", "custom")
    ]


@pytest.mark.unit
def test_graph_uses_custom_base_url_for_custom_openai_compatible(monkeypatch, tmp_path):
    import aitrade.graph.trading_graph as graph_module
    from aitrade.default_config import DEFAULT_CONFIG

    calls = []

    class FakeClient:
        def get_llm(self):
            return object()

    class FakeWorkflow:
        def compile(self):
            return object()

    class FakeGraphSetup:
        def __init__(self, *args, **kwargs):
            pass

        def setup_graph(self, selected_analysts):
            return FakeWorkflow()

    def fake_create_llm_client(provider, model, base_url=None, **kwargs):
        calls.append({"provider": provider, "model": model, "base_url": base_url, "kwargs": kwargs})
        return FakeClient()

    monkeypatch.setattr(graph_module, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(graph_module, "TradingMemoryLog", lambda config: object())
    monkeypatch.setattr(graph_module.AITradeGraph, "_create_tool_nodes", lambda self: {})
    monkeypatch.setattr(graph_module, "GraphSetup", FakeGraphSetup)

    config = deepcopy(DEFAULT_CONFIG)
    config.update(
        {
            "llm_provider": "custom_openai_compatible",
            "quick_think_llm": "qwen-fast",
            "deep_think_llm": "qwen-pro",
            "backend_url": None,
            "custom_base_url": "https://gateway.example.com/v1",
            "custom_api_key": "secret",
            "data_cache_dir": str(tmp_path / "cache"),
            "results_dir": str(tmp_path / "reports"),
        }
    )

    graph_module.AITradeGraph(selected_analysts=[], config=config)

    assert [call["base_url"] for call in calls] == [
        "https://gateway.example.com/v1",
        "https://gateway.example.com/v1",
    ]
