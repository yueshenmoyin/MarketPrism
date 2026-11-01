import pytest
from urllib.error import URLError


@pytest.mark.unit
def test_stocktwits_unavailable_message_is_chinese(monkeypatch):
    from aitrade.dataflows import stocktwits

    def raise_url_error(*args, **kwargs):
        raise URLError("Remote end closed connection without response")

    monkeypatch.setattr(stocktwits, "urlopen", raise_url_error)

    result = stocktwits.fetch_stocktwits_messages("AAPL")

    assert "数据暂不可用" in result
    assert "远端数据服务主动断开连接" in result
    assert "unavailable" not in result
    assert "Remote end closed connection" not in result
