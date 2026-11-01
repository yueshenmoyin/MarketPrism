import importlib.util

import pytest


@pytest.mark.unit
def test_aitrade_package_and_graph_entrypoint_are_primary_names():
    from aitrade.graph.trading_graph import AITradeGraph

    assert AITradeGraph.__name__ == "AITradeGraph"
    assert importlib.util.find_spec("aitrade") is not None
    assert importlib.util.find_spec("trading" + "agents") is None
