"""Tests for Hacker News (Algolia API) search client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.hackernews import HackerNewsClient
from monster_search.models import SearchResult


HN_RESPONSE = {
    "hits": [
        {
            "title": "Show HN: My New Search Tool",
            "url": "https://example.com/project",
            "author": "user123",
            "points": 150,
            "num_comments": 42,
            "created_at": "2026-03-15T10:00:00Z",
            "objectID": "12345",
            "_tags": ["story"],
        },
        {
            "title": "",
            "story_title": "Ask HN: Best search tools?",
            "url": "",
            "author": "searcher",
            "points": 75,
            "num_comments": 30,
            "created_at": "2026-03-14T08:00:00Z",
            "objectID": "67890",
            "_tags": ["comment"],
            "comment_text": "I've been looking for good search aggregators and found...",
        },
    ],
    "nbHits": 100,
}

HN_EMPTY = {"hits": [], "nbHits": 0}


@respx.mock
def test_hackernews_search():
    """Basic search returns SearchResults with correct fields."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_RESPONSE)
    )
    client = HackerNewsClient()
    results = client.search("search tool", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "hackernews"
    assert results[0].title == "Show HN: My New Search Tool"
    assert results[0].url == "https://example.com/project"
    assert "150 points" in results[0].snippet
    assert "42 comments" in results[0].snippet
    assert results[0].published == "2026-03-15T10:00:00Z"


@respx.mock
def test_hackernews_empty():
    """Empty hits returns empty list."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_EMPTY)
    )
    client = HackerNewsClient()
    results = client.search("xyznonexistent123")
    assert results == []


@respx.mock
def test_hackernews_error():
    """HTTP 500 raises HTTPStatusError."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(500)
    )
    client = HackerNewsClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_hackernews_timeout():
    """ReadTimeout raises TimeoutException."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = HackerNewsClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_hackernews_sends_correct_params():
    """Verify query and hitsPerPage params in request URL."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_EMPTY)
    )
    client = HackerNewsClient()
    client.search("rust async", max_results=10)

    request = respx.calls[0].request
    url_str = str(request.url)
    assert "query=rust" in url_str
    assert "hitsPerPage=10" in url_str


@respx.mock
def test_hackernews_comment_snippet():
    """comment_text is used for snippet when present."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_RESPONSE)
    )
    client = HackerNewsClient()
    results = client.search("search tools", max_results=5)

    # Second result is a comment
    assert "looking for good search aggregators" in results[1].snippet


@respx.mock
def test_hackernews_hn_url_fallback():
    """When url is empty, uses HN item URL with objectID."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_RESPONSE)
    )
    client = HackerNewsClient()
    results = client.search("search tools", max_results=5)

    # Second hit has empty url — should fall back to HN item URL
    assert results[1].url == "https://news.ycombinator.com/item?id=67890"


@respx.mock
@pytest.mark.asyncio
async def test_hackernews_async_search():
    """Async search works."""
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_RESPONSE)
    )
    client = HackerNewsClient()
    results = await client.asearch("search tool", max_results=5)

    assert len(results) == 2
    assert results[0].source == "hackernews"
    assert results[0].title == "Show HN: My New Search Tool"
