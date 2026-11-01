import sys
import types

import pandas as pd
import pytest


class FakeResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


@pytest.mark.unit
def test_a_share_sentiment_uses_eastmoney_comment_indicators(monkeypatch):
    from aitrade.dataflows import market_sentiment

    calls = []

    def frame(label):
        return pd.DataFrame(
            [
                {"日期": "2026-05-20", "指标": label, "数值": 10},
                {"日期": "2026-05-21", "指标": label, "数值": 12},
            ]
        )

    fake_akshare = types.SimpleNamespace(
        stock_comment_detail_zhpj_lspf_em=lambda symbol: calls.append(("score", symbol)) or frame("评分"),
        stock_comment_detail_scrd_focus_em=lambda symbol: calls.append(("focus", symbol)) or frame("关注"),
        stock_comment_detail_scrd_desire_em=lambda symbol: calls.append(("desire", symbol)) or frame("参与意愿"),
        stock_comment_detail_zlkp_jgcyd_em=lambda symbol: calls.append(("institution", symbol)) or frame("机构参与"),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr(
        market_sentiment.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            '<div class="listitem"><span>8</span><span>2</span>'
            '<span class="title"><a href="/news,600519,1.html">股吧讨论标题</a></span>'
            '<span>股友A</span><span>05-21 10:00</span></div>'
        ),
    )

    result = market_sentiment.get_a_share_sentiment("600519.SH", limit=1)

    assert calls == [
        ("score", "600519"),
        ("focus", "600519"),
        ("desire", "600519"),
        ("institution", "600519"),
    ]
    assert "东方财富千股千评" in result
    assert "历史评分" in result
    assert "用户关注指数" in result
    assert "市场参与意愿" in result
    assert "机构参与度" in result
    assert "东方财富股吧最新讨论" in result
    assert "股吧讨论标题" in result
    assert "2026-05-20" not in result
    assert "2026-05-21" in result


@pytest.mark.unit
def test_market_sentiment_errors_are_chinese():
    from aitrade.dataflows import market_sentiment

    section = market_sentiment._safe_section(
        "东方财富千股千评 - 历史评分",
        lambda: (_ for _ in ()).throw(ConnectionError("Remote end closed connection without response")),
        5,
    )

    assert "数据暂不可用" in section
    assert "远端数据服务主动断开连接" in section
    assert "ConnectionError" not in section
    assert "Remote end closed connection" not in section


@pytest.mark.unit
def test_hk_sentiment_uses_hong_kong_hot_rank(monkeypatch):
    from aitrade.dataflows import market_sentiment

    calls = []
    fake_akshare = types.SimpleNamespace(
        stock_hk_hot_rank_latest_em=lambda symbol: calls.append(("latest", symbol)) or pd.DataFrame(
            [{"代码": "00700", "名称": "腾讯控股", "最新排名": 3}]
        ),
        stock_hk_hot_rank_detail_em=lambda symbol: calls.append(("detail", symbol)) or pd.DataFrame(
            [{"日期": "2026-05-21", "排名": 3, "排名变化": -1}]
        ),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake_akshare)
    monkeypatch.setattr(
        market_sentiment.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            '<a href="/sc/stocks/analysis/stock-aafn-con/00700/AAFN/NOW.1/hk-stock-news">'
            "AAStocks港股新闻标题</a>"
        ),
    )

    result = market_sentiment.get_hk_stock_sentiment("0700.HK", limit=5)

    assert calls == [("latest", "00700"), ("detail", "00700")]
    assert "港股东方财富人气榜" in result
    assert "最新排名" in result
    assert "历史趋势" in result
    assert "AAStocks 港股本地新闻" in result
    assert "AAStocks港股新闻标题" in result
    assert "腾讯控股" in result


@pytest.mark.unit
def test_eastmoney_guba_posts_parse_latest_discussion(monkeypatch):
    from aitrade.dataflows import market_sentiment

    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse(
            '<div class="listitem">'
            "<span>21</span><span>1</span>"
            '<span class="title"><a href="/news,600519,1712379414.html">年轻人还喝白酒吗</a></span>'
            "<span>Babycci</span><span>05-21 06:30</span>"
            "</div>"
            '<div class="listitem">'
            "<span>10</span><span>0</span>"
            '<span class="title"><a href="/news,600519,1712383283.html">什么时候破1000再看</a></span>'
            "<span>神秘冷艳</span><span>05-21 06:43</span>"
            "</div>"
        )

    monkeypatch.setattr(market_sentiment.requests, "get", fake_get)

    result = market_sentiment.fetch_eastmoney_guba_posts("600519.SH", limit=1)

    assert requested == ["https://guba.eastmoney.com/list,600519.html"]
    assert "东方财富股吧最新讨论" in result
    assert "年轻人还喝白酒吗" in result
    assert "Babycci" in result
    assert "1712379414" in result
    assert "什么时候破1000再看" not in result


@pytest.mark.unit
def test_aastocks_hk_news_parses_local_news(monkeypatch):
    from aitrade.dataflows import market_sentiment

    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return FakeResponse(
            '<a href="/sc/stocks/analysis/stock-aafn-con/00700/AAFN/NOW.1525678/hk-stock-news">'
            "腾讯周四回购111.9万股 涉资5亿元</a>"
            '<a href="/sc/stocks/analysis/stock-aafn-con/00700/AAFN/NOW.1525656/hk-stock-news">'
            "腾讯会议上线AI同传</a>"
        )

    monkeypatch.setattr(market_sentiment.requests, "get", fake_get)

    result = market_sentiment.fetch_aastocks_hk_news("0700.HK", limit=1)

    assert requested == ["https://m.aastocks.com/sc/stocks/analysis/stock-aafn/00700/0/hk-stock-news/1"]
    assert "AAStocks 港股本地新闻" in result
    assert "腾讯周四回购111.9万股" in result
    assert "NOW.1525678" in result
    assert "腾讯会议上线AI同传" not in result
