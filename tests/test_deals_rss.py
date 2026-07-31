"""Tests for DealsRSSClient — multi-feed RSS deal aggregator."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from monster_search.clients.deals_rss import (
    DealsRSSClient,
    _matches_query,
    _strip_html,
)
from monster_search.models import SearchResult


def _make_entry(
    title: str = "Deal Title",
    link: str = "https://example.com/deal",
    description: str = "A great deal on tech.",
    published: str = "Mon, 07 Apr 2026 12:00:00 GMT",
) -> dict:
    """Helper to build a mock feedparser entry dict."""
    return {
        "title": title,
        "link": link,
        "description": description,
        "published": published,
    }


def _make_feed(entries: list[dict]) -> MagicMock:
    """Build a mock feedparser.FeedParserDict with given entries."""
    feed = MagicMock()
    feed.entries = [MagicMock(**e, **{"get.side_effect": e.get}) for e in entries]
    return feed


# === Basic Aggregation ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_basic_aggregation(mock_parse: MagicMock) -> None:
    """Results from multiple feeds are aggregated."""
    feed_a = _make_feed([
        _make_entry(
            title="[GPU] RTX 5090 $999 - Amazing Deal",
            link="https://reddit.com/r/buildapcsales/1",
            published="Tue, 08 Apr 2026 10:00:00 GMT",
        ),
    ])
    feed_b = _make_feed([
        _make_entry(
            title="RTX 5090 Founders Edition $1,049",
            link="https://dealnews.com/deal/1",
            published="Mon, 07 Apr 2026 08:00:00 GMT",
        ),
    ])
    feed_empty = _make_feed([])

    mock_parse.side_effect = [
        feed_a, feed_b, feed_empty,
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("RTX 5090")

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    # Newest first
    assert results[0].source == "r/buildapcsales"
    assert results[1].source == "dealnews"


# === Query Filtering ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_query_filtering(mock_parse: MagicMock) -> None:
    """Only entries matching the query are returned."""
    feed = _make_feed([
        _make_entry(title="[GPU] RTX 5090 $999", link="https://example.com/1"),
        _make_entry(title="[CPU] Ryzen 9950X $549", link="https://example.com/2"),
        _make_entry(title="[SSD] Samsung 990 Pro $89", link="https://example.com/3"),
    ])
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("RTX")

    assert len(results) == 1
    assert "RTX" in results[0].title


# === Price Extraction ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_price_extraction(mock_parse: MagicMock) -> None:
    """Price is extracted from title via regex."""
    feed = _make_feed([
        _make_entry(title="[GPU] RTX 5090 $999.99 at Newegg", link="https://example.com/1"),
        _make_entry(title="[RAM] DDR5 Kit $129 - Amazon", link="https://example.com/2"),
        _make_entry(title="[Monitor] LG OLED No Price Listed", link="https://example.com/3"),
    ])
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("RTX RAM Monitor")

    prices = {r.title.split("]")[0] + "]": r.price for r in results}
    assert prices["[GPU]"] == "$999.99"
    assert prices["[RAM]"] == "$129"
    assert prices["[Monitor]"] is None


# === Empty Feeds ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_empty_feeds_return_empty(mock_parse: MagicMock) -> None:
    """All empty feeds produce an empty result list."""
    mock_parse.return_value = _make_feed([])

    client = DealsRSSClient()
    results = client.search("nonexistent product xyz")

    assert results == []


# === max_results Limit ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_max_results_limit(mock_parse: MagicMock) -> None:
    """Results are capped at max_results."""
    entries = [
        _make_entry(
            title=f"[Deal] Widget {i} $10",
            link=f"https://example.com/{i}",
            published=f"Mon, 0{i} Apr 2026 12:00:00 GMT",
        )
        for i in range(1, 6)
    ]
    feed = _make_feed(entries)
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("Widget", max_results=2)

    assert len(results) == 2


# === Sorting by Date ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_sorted_newest_first(mock_parse: MagicMock) -> None:
    """Results are sorted by published date, newest first."""
    feed = _make_feed([
        _make_entry(
            title="[SSD] Old Deal $50",
            link="https://example.com/old",
            published="Mon, 01 Apr 2026 08:00:00 GMT",
        ),
        _make_entry(
            title="[SSD] New Deal $40",
            link="https://example.com/new",
            published="Wed, 09 Apr 2026 12:00:00 GMT",
        ),
    ])
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("Deal")

    assert results[0].title == "[SSD] New Deal $40"
    assert results[1].title == "[SSD] Old Deal $50"


# === Snippet HTML Stripping ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_snippet_html_stripped(mock_parse: MagicMock) -> None:
    """HTML tags are stripped from the description snippet."""
    feed = _make_feed([
        _make_entry(
            title="[GPU] Card Deal $200",
            link="https://example.com/1",
            description='<a href="http://x.com">Click here</a> for <b>great savings</b>!',
        ),
    ])
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("Card")

    assert "<" not in results[0].snippet
    assert "great savings" in results[0].snippet


# === in_stock Always None ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_in_stock_is_none(mock_parse: MagicMock) -> None:
    """in_stock is always None for RSS deals."""
    feed = _make_feed([
        _make_entry(title="[CPU] Chip $100", link="https://example.com/1"),
    ])
    mock_parse.side_effect = [
        feed, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = client.search("Chip")

    assert results[0].in_stock is None


# === Async Search ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
@pytest.mark.asyncio
async def test_async_search(mock_parse: MagicMock) -> None:
    """Async search returns the same results as sync."""
    feed_a = _make_feed([
        _make_entry(
            title="[GPU] RTX 5090 $999",
            link="https://reddit.com/r/buildapcsales/1",
            published="Tue, 08 Apr 2026 10:00:00 GMT",
        ),
    ])
    mock_parse.side_effect = [
        feed_a, _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]), _make_feed([]),
        _make_feed([]), _make_feed([]),
    ]

    client = DealsRSSClient()
    results = await client.asearch("RTX 5090")

    assert len(results) == 1
    assert results[0].source == "r/buildapcsales"
    assert results[0].price == "$999"


# === New Feed Sources ===


@patch("monster_search.clients.deals_rss.feedparser.parse")
def test_new_feed_sources_in_results(mock_parse: MagicMock) -> None:
    """All 5 new feed source names appear when their feeds return matches."""
    feed_empty = _make_feed([])
    # Original 3 feeds return nothing
    feed_dealnews_computers = _make_feed([
        _make_entry(
            title="Laptop Deal $499",
            link="https://dealnews.com/computers/1",
            published="Wed, 09 Apr 2026 10:00:00 GMT",
        ),
    ])
    feed_dealnews_electronics = _make_feed([
        _make_entry(
            title="TV Deal $299",
            link="https://dealnews.com/electronics/1",
            published="Wed, 09 Apr 2026 09:00:00 GMT",
        ),
    ])
    feed_bensbargains = _make_feed([
        _make_entry(
            title="Headphone Deal $59",
            link="https://bensbargains.com/1",
            published="Wed, 09 Apr 2026 08:00:00 GMT",
        ),
    ])
    feed_r_deals = _make_feed([
        _make_entry(
            title="Keyboard Deal $39",
            link="https://reddit.com/r/deals/1",
            published="Wed, 09 Apr 2026 07:00:00 GMT",
        ),
    ])
    feed_r_gamedeals = _make_feed([
        _make_entry(
            title="Game Deal $9",
            link="https://reddit.com/r/GameDeals/1",
            published="Wed, 09 Apr 2026 06:00:00 GMT",
        ),
    ])

    mock_parse.side_effect = [
        feed_empty, feed_empty, feed_empty,  # original 3
        feed_dealnews_computers, feed_dealnews_electronics,
        feed_bensbargains, feed_r_deals, feed_r_gamedeals,
    ]

    client = DealsRSSClient()
    results = client.search("Deal")

    sources = {r.source for r in results}
    assert "dealnews-computers" in sources
    assert "dealnews-electronics" in sources
    assert "bensbargains" in sources
    assert "r/deals" in sources
    assert "r/GameDeals" in sources
    assert len(results) == 5


# === Helper Unit Tests ===


def test_strip_html_removes_tags() -> None:
    """_strip_html removes HTML tags correctly."""
    assert _strip_html("<b>bold</b> text") == "bold text"
    assert _strip_html('<a href="url">link</a>') == "link"
    assert _strip_html("no tags") == "no tags"
    assert _strip_html("") == ""


def test_matches_query_basic() -> None:
    """_matches_query filters on 3+ character words."""
    assert _matches_query("rtx 5090 gpu deal", "RTX 5090") is True
    assert _matches_query("rtx 5090 gpu deal", "cpu intel") is False
    # Short words are skipped when longer words exist
    assert _matches_query("an awesome deal", "awesome") is True
    # But if ALL words are short, they are used as-is
    assert _matches_query("an awesome deal", "an") is True
    assert _matches_query("4k tv on sale", "4K TV") is True
    assert _matches_query("pc gaming setup", "PC") is True
    assert _matches_query("nothing matches here", "zz") is False


def test_matches_query_case_insensitive() -> None:
    """Query matching is case-insensitive."""
    assert _matches_query("rtx 5090 founders edition", "RTX") is True
    assert _matches_query("RTX 5090 FOUNDERS EDITION", "rtx") is True
