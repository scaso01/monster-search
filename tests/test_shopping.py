"""Tests for the shopping engine (SearXNG shopping-category wrapper)."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.shopping import ShoppingSearchClient
from monster_search.config import Config
from monster_search.models import SearchResult


SEARXNG_SHOPPING = {
    "results": [
        {
            "title": "Widget Pro 3000",
            "url": "https://example.com/widget-pro",
            "content": "The best widget money can buy.",
        },
        {
            "title": "Widget Lite",
            "url": "https://example.com/widget-lite",
            "content": "A cheaper widget.",
        },
    ]
}

SEARXNG_EMPTY: dict = {"results": []}


@respx.mock
def test_shopping_search_returns_results():
    """Basic search maps SearXNG hits onto SearchResult."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_SHOPPING)
    )
    results = ShoppingSearchClient().search("widget")

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].title == "Widget Pro 3000"
    assert results[0].url == "https://example.com/widget-pro"


@respx.mock
def test_shopping_requests_the_shopping_category():
    """The whole point of this wrapper is category=shopping, so pin it."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_EMPTY)
    )
    ShoppingSearchClient().search("laptop")

    url = str(respx.calls[0].request.url)
    assert "categories=shopping" in url


@respx.mock
def test_shopping_empty_results():
    """No hits gives an empty list rather than raising."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_EMPTY)
    )
    assert ShoppingSearchClient().search("nothing-matches-this") == []


@respx.mock
def test_shopping_max_results_defaults_to_config():
    """An unset max_results falls back to Config.max_results, not None."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_SHOPPING)
    )
    client = ShoppingSearchClient(config=Config(max_results=1))
    results = client.search("widget")

    assert len(results) == 1


@respx.mock
def test_shopping_explicit_max_results_wins():
    """An explicit max_results overrides the config default."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_SHOPPING)
    )
    client = ShoppingSearchClient(config=Config(max_results=1))
    results = client.search("widget", max_results=2)

    assert len(results) == 2


@respx.mock
def test_shopping_http_error_propagates():
    """A 500 from SearXNG is not swallowed."""
    respx.get("http://localhost:8080/search").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        ShoppingSearchClient().search("widget")


@respx.mock
@pytest.mark.asyncio
async def test_shopping_async_search():
    """Async path behaves like the sync one."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_SHOPPING)
    )
    results = await ShoppingSearchClient().asearch("widget")

    assert len(results) == 2
    assert results[0].url == "https://example.com/widget-pro"


@respx.mock
@pytest.mark.asyncio
async def test_shopping_async_requests_the_shopping_category():
    """The async path must also pin the category."""
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=SEARXNG_EMPTY)
    )
    await ShoppingSearchClient().asearch("laptop")

    url = str(respx.calls[0].request.url)
    assert "categories=shopping" in url
