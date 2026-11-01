import pytest

from aitrade.default_config import DEFAULT_CONFIG
from aitrade.dataflows.config import set_config


@pytest.fixture(autouse=True)
def _reset_config():
    set_config(DEFAULT_CONFIG)
    yield
    set_config(DEFAULT_CONFIG)


@pytest.mark.unit
def test_a_share_sentiment_uses_mainland_sources_without_us_social(monkeypatch):
    from aitrade.agents.analysts import sentiment_analyst

    monkeypatch.setattr(sentiment_analyst.get_news, "func", lambda *args: "A-share news block")
    monkeypatch.setattr(sentiment_analyst, "get_a_share_sentiment", lambda *args: "Eastmoney A-share sentiment block")
    monkeypatch.setattr(sentiment_analyst, "fetch_stocktwits_messages", lambda *args, **kwargs: "stocktwits")
    monkeypatch.setattr(sentiment_analyst, "fetch_reddit_posts", lambda *args, **kwargs: "reddit")
    set_config({"primary_market": "cn_a_share"})

    bundle = sentiment_analyst.build_sentiment_source_bundle("600519.SH", "2026-05-14", "2026-05-21")
    prompt = sentiment_analyst._build_system_message(
        ticker="600519.SH",
        start_date="2026-05-14",
        end_date="2026-05-21",
        bundle=bundle,
    )

    assert "A-share mainland news and announcement sentiment" in prompt
    assert "A-share news block" in prompt
    assert "Mainland investor sentiment indicators" in prompt
    assert "Eastmoney Guba discussion" in prompt
    assert "Eastmoney A-share sentiment block" in prompt
    assert "StockTwits" not in prompt
    assert "Reddit" not in prompt


@pytest.mark.unit
def test_hk_sentiment_uses_hong_kong_sources_without_us_social(monkeypatch):
    from aitrade.agents.analysts import sentiment_analyst

    monkeypatch.setattr(sentiment_analyst.get_news, "func", lambda *args: "HK news block")
    monkeypatch.setattr(sentiment_analyst, "get_hk_stock_sentiment", lambda *args: "HK popularity sentiment block")
    monkeypatch.setattr(sentiment_analyst, "fetch_stocktwits_messages", lambda *args, **kwargs: "stocktwits")
    monkeypatch.setattr(sentiment_analyst, "fetch_reddit_posts", lambda *args, **kwargs: "reddit")
    set_config({"primary_market": "hk_stock"})

    bundle = sentiment_analyst.build_sentiment_source_bundle("0700.HK", "2026-05-14", "2026-05-21")
    prompt = sentiment_analyst._build_system_message(
        ticker="0700.HK",
        start_date="2026-05-14",
        end_date="2026-05-21",
        bundle=bundle,
    )

    assert "Hong Kong market news sentiment" in prompt
    assert "HK news block" in prompt
    assert "Hong Kong local popularity sentiment" in prompt
    assert "AAStocks local news" in prompt
    assert "HK popularity sentiment block" in prompt
    assert "StockTwits" not in prompt
    assert "Reddit" not in prompt


@pytest.mark.unit
def test_us_sentiment_keeps_us_news_stocktwits_and_reddit(monkeypatch):
    from aitrade.agents.analysts import sentiment_analyst

    monkeypatch.setattr(sentiment_analyst.get_news, "func", lambda *args: "US news block")
    monkeypatch.setattr(sentiment_analyst, "fetch_stocktwits_messages", lambda *args, **kwargs: "stocktwits block")
    monkeypatch.setattr(sentiment_analyst, "fetch_reddit_posts", lambda *args, **kwargs: "reddit block")
    set_config({"primary_market": "us_stock"})

    bundle = sentiment_analyst.build_sentiment_source_bundle("AAPL", "2026-05-14", "2026-05-21")
    prompt = sentiment_analyst._build_system_message(
        ticker="AAPL",
        start_date="2026-05-14",
        end_date="2026-05-21",
        bundle=bundle,
    )

    assert "US market sentiment" in prompt
    assert "US news block" in prompt
    assert "StockTwits messages" in prompt
    assert "stocktwits block" in prompt
    assert "Reddit posts" in prompt
    assert "reddit block" in prompt
