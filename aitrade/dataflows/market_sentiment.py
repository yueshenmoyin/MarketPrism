from __future__ import annotations

from typing import Callable
from urllib.parse import urljoin

import pandas as pd
import requests
from parsel import Selector

from aitrade.dataflows.china_stock import format_user_facing_error, normalize_cn_stock_symbol


def _akshare():
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("缺少 akshare，无法获取本地化情绪数据。") from exc
    return ak


def _cn_code(symbol: str) -> str:
    return normalize_cn_stock_symbol(symbol).split(".", 1)[0]


def _hk_code(symbol: str) -> str:
    raw = symbol.strip().upper()
    if raw.endswith(".HK"):
        raw = raw[:-3]
    return raw.zfill(5)


def _clean_text(text: str | None) -> str:
    return " ".join((text or "").split())


def _format_dataframe_section(title: str, df: pd.DataFrame, limit: int) -> str:
    if df.empty:
        return f"## {title}\n\n未找到可用数据。"
    visible = df.tail(max(1, limit))
    return f"## {title}\n\n{visible.to_csv(index=False)}"


def _safe_section(title: str, fetcher: Callable[[], pd.DataFrame], limit: int) -> str:
    try:
        return _format_dataframe_section(title, fetcher(), limit)
    except Exception as exc:
        return f"## {title}\n\n数据暂不可用：{format_user_facing_error(exc)}"


def fetch_eastmoney_guba_posts(symbol: str, limit: int = 10, timeout: float = 10.0) -> str:
    """Fetch latest Eastmoney Guba discussion titles for a mainland A-share ticker."""
    code = _cn_code(symbol)
    normalized = normalize_cn_stock_symbol(symbol)
    url = f"https://guba.eastmoney.com/list,{code}.html"

    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        response.raise_for_status()
        selector = Selector(text=response.text)
    except Exception as exc:
        return f"## 东方财富股吧最新讨论\n\n数据暂不可用：{format_user_facing_error(exc)}"

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in selector.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " listitem ")]'):
        link = item.xpath('.//a[contains(@href, "/news,")][1]')
        href = link.attrib.get("href", "")
        if f"/news,{code}," not in href:
            continue

        title = _clean_text(link.xpath("string(.)").get())
        if not title or href in seen:
            continue
        seen.add(href)

        texts = [_clean_text(value) for value in item.xpath(".//text()").getall()]
        texts = [value for value in texts if value]
        title_index = texts.index(title) if title in texts else 2
        rows.append(
            {
                "阅读": texts[0] if len(texts) > 0 else "",
                "评论": texts[1] if len(texts) > 1 else "",
                "标题": title,
                "作者": texts[title_index + 1] if len(texts) > title_index + 1 else "",
                "时间": texts[title_index + 2] if len(texts) > title_index + 2 else "",
                "链接": urljoin("https://guba.eastmoney.com", href),
            }
        )
        if len(rows) >= limit:
            break

    if not rows:
        return f"## 东方财富股吧最新讨论\n\n未找到 {normalized} 的股吧讨论。"

    df = pd.DataFrame(rows)
    return f"## 东方财富股吧最新讨论: {normalized}\n\n{df.to_csv(index=False)}"


def fetch_aastocks_hk_news(symbol: str, limit: int = 8, timeout: float = 10.0) -> str:
    """Fetch simplified-Chinese AAStocks local news headlines for a Hong Kong ticker."""
    code = _hk_code(symbol)
    url = f"https://m.aastocks.com/sc/stocks/analysis/stock-aafn/{code}/0/hk-stock-news/1"

    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
        response.raise_for_status()
        selector = Selector(text=response.text)
    except Exception as exc:
        return f"## AAStocks 港股本地新闻\n\n数据暂不可用：{format_user_facing_error(exc)}"

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in selector.xpath('//a[contains(@href, "stock-aafn-con")]'):
        href = link.attrib.get("href", "")
        title = _clean_text(link.xpath("string(.)").get())
        if not title or href in seen:
            continue
        seen.add(href)
        rows.append(
            {
                "标题": title,
                "链接": urljoin("https://m.aastocks.com", href),
            }
        )
        if len(rows) >= limit:
            break

    if not rows:
        return f"## AAStocks 港股本地新闻\n\n未找到 {code}.HK 的 AAStocks 新闻。"

    df = pd.DataFrame(rows)
    return f"## AAStocks 港股本地新闻: {code}.HK\n\n{df.to_csv(index=False)}"


def get_a_share_sentiment(symbol: str, limit: int = 10) -> str:
    """Fetch mainland A-share local sentiment indicators from Eastmoney via AkShare."""
    ak = _akshare()
    code = _cn_code(symbol)
    normalized = normalize_cn_stock_symbol(symbol)

    sections = [
        _safe_section("东方财富千股千评 - 历史评分", lambda: ak.stock_comment_detail_zhpj_lspf_em(symbol=code), limit),
        _safe_section("东方财富千股千评 - 用户关注指数", lambda: ak.stock_comment_detail_scrd_focus_em(symbol=code), limit),
        _safe_section("东方财富千股千评 - 市场参与意愿", lambda: ak.stock_comment_detail_scrd_desire_em(symbol=code), limit),
        _safe_section("东方财富千股千评 - 机构参与度", lambda: ak.stock_comment_detail_zlkp_jgcyd_em(symbol=code), limit),
        fetch_eastmoney_guba_posts(normalized, limit=limit),
    ]
    return f"# A股东方财富千股千评情绪数据: {normalized}\n\n" + "\n\n".join(sections)


def get_hk_stock_sentiment(symbol: str, limit: int = 10) -> str:
    """Fetch Hong Kong stock popularity sentiment indicators from Eastmoney via AkShare."""
    ak = _akshare()
    code = _hk_code(symbol)

    sections = [
        _safe_section("港股东方财富人气榜 - 最新排名", lambda: ak.stock_hk_hot_rank_latest_em(symbol=code), limit),
        _safe_section("港股东方财富人气榜 - 历史趋势", lambda: ak.stock_hk_hot_rank_detail_em(symbol=code), limit),
        fetch_aastocks_hk_news(code, limit=limit),
    ]
    return f"# 港股东方财富人气榜情绪数据: {code}.HK\n\n" + "\n\n".join(sections)
