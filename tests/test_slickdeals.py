"""Tests for Slickdeals RSS search client."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from monster_search.clients.slickdeals import SlickdealsClient, _extract_price, _strip_html
from monster_search.models import SearchResult


def _make_feed(entries: list[dict]) -> MagicMock:
    """Build a mock feedparser.FeedParserDict with given entries."""
    feed = MagicMock()
    mock_entries = []
    for e in entries:
        entry = MagicMock()
        entry.get = e.get
        mock_entries.append(entry)
    feed.entries = mock_entries
    return feed


FEED_ENTRIES = [
    {
        "title": "Samsung 990 EVO Plus 2TB SSD - $109.99",
        "link": "https://slickdeals.net/f/12345-samsung-990-evo",
        "description": "<p>Great deal on a <b>fast NVMe SSD</b>. Regular price $159.99.</p>",
        "published": "Sat, 12 Apr 2026 10:00:00 GMT",
    },
    {
        "title": "Sony WH-1000XM5 Headphones $248",
        "link": "https://slickdeals.net/f/67890-sony-headphones",
        "description": "<div>Noise cancelling headphones on sale.</div>",
        "published": "Fri, 11 Apr 2026 08:30:00 GMT",
    },
    {
        "title": "Free Roku Streaming Stick",
        "link": "https://slickdeals.net/f/11111-free-roku",
        "description": "Get a free Roku stick with signup. No price listed.",
        "published": "Thu, 10 Apr 2026 12:00:00 GMT",
    },
]


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_search_extracts_prices(mock_parse):
    """Basic search returns SearchResults with prices extracted from titles."""
    mock_parse.return_value = _make_feed(FEED_ENTRIES)

    client = SlickdealsClient()
    results = client.search("ssd", max_results=5)

    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)

    # First result: price in title
    assert results[0].source == "slickdeals"
    assert results[0].title == "Samsung 990 EVO Plus 2TB SSD - $109.99"
    assert results[0].url == "https://slickdeals.net/f/12345-samsung-990-evo"
    assert results[0].price == "$109.99"
    assert results[0].published == "Sat, 12 Apr 2026 10:00:00 GMT"

    # Second result: price in title
    assert results[1].price == "$248"
    assert results[1].title == "Sony WH-1000XM5 Headphones $248"


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_no_price_returns_none(mock_parse):
    """Entry without any price in title or description returns price=None."""
    mock_parse.return_value = _make_feed([FEED_ENTRIES[2]])

    client = SlickdealsClient()
    results = client.search("free stuff", max_results=5)

    assert len(results) == 1
    assert results[0].price is None
    assert results[0].title == "Free Roku Streaming Stick"


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_empty_feed(mock_parse):
    """Empty feed returns empty list."""
    mock_parse.return_value = _make_feed([])

    client = SlickdealsClient()
    results = client.search("xyznonexistent")

    assert results == []


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_strips_html_from_snippet(mock_parse):
    """HTML tags are removed from description in snippet."""
    mock_parse.return_value = _make_feed([FEED_ENTRIES[0]])

    client = SlickdealsClient()
    results = client.search("ssd", max_results=5)

    assert "<p>" not in results[0].snippet
    assert "<b>" not in results[0].snippet
    assert "fast NVMe SSD" in results[0].snippet


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_in_stock_is_none(mock_parse):
    """in_stock is always None for RSS results."""
    mock_parse.return_value = _make_feed(FEED_ENTRIES)

    client = SlickdealsClient()
    results = client.search("deals", max_results=5)

    for r in results:
        assert r.in_stock is None


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_max_results_limits_output(mock_parse):
    """max_results limits number of returned results."""
    mock_parse.return_value = _make_feed(FEED_ENTRIES)

    client = SlickdealsClient()
    results = client.search("deals", max_results=2)

    assert len(results) == 2


@patch("monster_search.clients.slickdeals.feedparser.parse")
def test_slickdeals_price_from_description_fallback(mock_parse):
    """Price is extracted from description when not in title."""
    entry_no_title_price = {
        "title": "Great Deal on Monitor",
        "link": "https://slickdeals.net/f/99999-monitor",
        "description": "This monitor is on sale for <b>$299.99</b> at Amazon.",
        "published": "Wed, 09 Apr 2026 09:00:00 GMT",
    }
    mock_parse.return_value = _make_feed([entry_no_title_price])

    client = SlickdealsClient()
    results = client.search("monitor", max_results=5)

    assert results[0].price == "$299.99"


@patch("monster_search.clients.slickdeals.feedparser.parse")
@pytest.mark.asyncio
async def test_slickdeals_async_search(mock_parse):
    """Async search returns same results as sync."""
    mock_parse.return_value = _make_feed(FEED_ENTRIES)

    client = SlickdealsClient()
    results = await client.asearch("ssd", max_results=5)

    assert len(results) == 3
    assert results[0].source == "slickdeals"
    assert results[0].price == "$109.99"


def test_extract_price_helper():
    """_extract_price finds dollar amounts correctly."""
    assert _extract_price("Sale for $29.99 today") == "$29.99"
    assert _extract_price("$1,299.99 laptop deal") == "$1,299.99"
    assert _extract_price("$5 off coupon") == "$5"
    assert _extract_price("No price here") is None
    assert _extract_price("") is None


def test_strip_html_helper():
    """_strip_html removes all HTML tags."""
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html("No tags here") == "No tags here"
    assert _strip_html("") == ""
