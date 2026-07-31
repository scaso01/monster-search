from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_RESPONSE = {
    "query": "python asyncio",
    "number_of_results": 2,
    "results": [
        {
            "url": "https://docs.python.org/3/library/asyncio.html",
            "title": "asyncio — Asynchronous I/O",
            "content": "asyncio is a library to write concurrent code.",
            "engine": "duckduckgo",
            "engines": ["duckduckgo", "brave"],
            "score": 8.5,
            "category": "general",
            "publishedDate": "2024-01-15T00:00:00",
            "positions": [1, 2],
        },
        {
            "url": "https://realpython.com/async-io-python/",
            "title": "Async IO in Python",
            "content": "A complete walkthrough of async IO.",
            "engine": "brave",
            "engines": ["brave"],
            "score": 6.2,
            "category": "general",
            "publishedDate": None,
            "positions": [3],
        },
    ],
    "suggestions": ["python async await"],
    "answers": [],
    "infoboxes": [],
    "corrections": [],
    "unresponsive_engines": [],
}


@respx.mock
def test_searxng_search():
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SearXNGClient()
    results = client.search("python asyncio")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "asyncio — Asynchronous I/O"
    assert results[0].source == "searxng"
    assert results[0].engine == "duckduckgo"
    assert results[0].score == 8.5


@respx.mock
def test_searxng_search_with_category():
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SearXNGClient()
    client.search("python", category="news")
    request = respx.calls[0].request
    assert "categories=news" in str(request.url)


@respx.mock
def test_searxng_search_max_results():
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SearXNGClient()
    results = client.search("python asyncio", max_results=1)
    assert len(results) == 1


@respx.mock
def test_searxng_search_custom_config():
    respx.get("http://localhost:9090/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(searxng_url="http://localhost:9090")
    client = SearXNGClient(config=config)
    results = client.search("test")
    assert len(results) == 2


@respx.mock
def test_searxng_search_handles_error():
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(500)
    )
    client = SearXNGClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
@pytest.mark.asyncio
async def test_searxng_async_search():
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SearXNGClient()
    results = await client.asearch("python asyncio")
    assert len(results) == 2
    assert results[0].source == "searxng"
