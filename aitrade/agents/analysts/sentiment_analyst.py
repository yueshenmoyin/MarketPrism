"""Market-aware sentiment analyst for the selected ticker.

The analyst pre-fetches sentiment evidence before the LLM is invoked and
injects those sources into the prompt as structured blocks. Source selection
is market-specific so mainland A-share and Hong Kong reports do not inherit
US retail-social context.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from aitrade.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from aitrade.dataflows.china_stock import is_cn_stock_symbol
from aitrade.dataflows.config import get_config
from aitrade.dataflows.market_sentiment import get_a_share_sentiment, get_hk_stock_sentiment
from aitrade.dataflows.reddit import fetch_reddit_posts
from aitrade.dataflows.stocktwits import fetch_stocktwits_messages


@dataclass(frozen=True)
class SentimentSourceBlock:
    title: str
    description: str
    content: str


@dataclass(frozen=True)
class SentimentSourceBundle:
    market: str
    market_label: str
    blocks: tuple[SentimentSourceBlock, ...]
    guidance: str


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def _resolve_sentiment_market(ticker: str) -> str:
    upper = ticker.strip().upper()
    if upper.endswith(".HK"):
        return "hk_stock"
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return "cn_a_share"

    configured = get_config().get("primary_market")
    if configured in {"hk_stock", "us_stock"}:
        return configured

    if is_cn_stock_symbol(ticker):
        return "cn_a_share"
    return "us_stock"


def build_sentiment_source_bundle(ticker: str, start_date: str, end_date: str) -> SentimentSourceBundle:
    market = _resolve_sentiment_market(ticker)
    news_block = get_news.func(ticker, start_date, end_date)

    if market == "cn_a_share":
        return SentimentSourceBundle(
            market=market,
            market_label="A-share market sentiment",
            blocks=(
                SentimentSourceBlock(
                    title="A-share mainland news and announcement sentiment",
                    description=(
                        "Mainland China company news, exchange-facing disclosures, and local market framing. "
                        "Treat this as the primary sentiment signal for A-share analysis."
                    ),
                    content=news_block,
                ),
                SentimentSourceBlock(
                    title="Mainland investor sentiment indicators",
                    description=(
                        "Eastmoney A-share comment and popularity indicators, including historical score, "
                        "attention index, participation willingness, institutional participation, and "
                        "Eastmoney Guba discussion titles."
                    ),
                    content=get_a_share_sentiment(ticker),
                ),
            ),
            guidance=(
                "Analyze the evidence using mainland China equity-market context. Focus on policy tone, "
                "industry regulation, company announcements, local media framing, and investor risk appetite. "
                "Use Eastmoney indicators as local investor sentiment evidence, and do not invent unavailable "
                "US social-platform evidence; if the source coverage is thin, say so."
            ),
        )

    if market == "hk_stock":
        return SentimentSourceBundle(
            market=market,
            market_label="Hong Kong market sentiment",
            blocks=(
                SentimentSourceBlock(
                    title="Hong Kong market news sentiment",
                    description=(
                        "Hong Kong listed-company and market news. Treat this as the primary sentiment signal "
                        "for HK tickers unless dedicated local social/forum data is added later."
                    ),
                    content=news_block,
                ),
                SentimentSourceBlock(
                    title="Hong Kong local popularity sentiment",
                    description=(
                        "Eastmoney Hong Kong stock popularity ranking and trend data, plus AAStocks local news "
                        "headlines. Treat these as local attention and market-framing signals, not as direct "
                        "buy/sell signals."
                    ),
                    content=get_hk_stock_sentiment(ticker),
                ),
            ),
            guidance=(
                "Analyze the evidence using Hong Kong market context. Consider mainland policy spillover, "
                "HK liquidity, sector narratives, ADR/China-tech linkage where relevant, and global risk appetite. "
                "Use Hong Kong popularity data as local attention evidence, and do not invent unavailable "
                "US social-platform evidence; if the source coverage is thin, say so."
            ),
        )

    return SentimentSourceBundle(
        market="us_stock",
        market_label="US market sentiment",
        blocks=(
            SentimentSourceBlock(
                title="Yahoo Finance news headlines",
                description="Institutional and media framing for the ticker over the lookback window.",
                content=news_block,
            ),
            SentimentSourceBlock(
                title="StockTwits messages",
                description="Retail-trader posts indexed by cashtag, including Bullish/Bearish tags when present.",
                content=fetch_stocktwits_messages(ticker, limit=30),
            ),
            SentimentSourceBlock(
                title="Reddit posts",
                description="Community discussion from finance subreddits, weighted by engagement and context.",
                content=fetch_reddit_posts(ticker),
            ),
        ),
        guidance=(
            "Analyze cross-source alignment and divergence between news framing, fast retail sentiment, "
            "and community discussion. Weight sample size and engagement; separate events from opinions."
        ),
    )


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph."""

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)
        bundle = build_sentiment_source_bundle(ticker, start_date, end_date)

        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            bundle=bundle,
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    bundle: SentimentSourceBundle,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    source_sections = "\n\n".join(
        f"### {block.title}\n{block.description}\n\n<start_of_source>\n{block.content}\n<end_of_source>"
        for block in bundle.blocks
    )
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive {bundle.market_label} report for {ticker} covering the period from {start_date} to {end_date}. Use only the market-appropriate sources collected below.

## Data sources (pre-fetched, in this prompt)

{source_sections}

## How to analyze this data

1. **Use the right market context.** {bundle.guidance}

2. **Look for source alignment and divergence.** If multiple source blocks are available, compare whether they point in the same direction or conflict.

3. **Distinguish opinion from event.** A reported company event, regulatory change, or earnings item should be weighted differently from commentary or narrative framing.

4. **Identify recurring narrative themes.** What topic keeps coming up across available sources? That's the dominant narrative driving current sentiment.

5. **Be honest about data limits.** If one or more sources returned an unavailable placeholder or very little coverage, explicitly lower confidence.

6. **Identify catalysts and risks** that emerge from the evidence.

7. **Past sentiment is not predictive.** Frame your conclusions as a signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** - Bullish / Bearish / Neutral / Mixed, with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** - what each available source is telling you, with specific evidence.
3. **Divergences, alignments, and key narratives** across available sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

{get_language_instruction()}"""


def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`."""
    import warnings

    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
