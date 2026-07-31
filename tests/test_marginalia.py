from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.marginalia import MarginaliaClient
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_RESPONSE = {
    "license": "CC-BY-NC-SA 4.0",
    "page": 1,
    "pages": 3,
    "query": "search engines",
    "results": [
        {
            "url": "https://bottlepy.org/docs/dev/",
            "title": "About Marginalia",
            "description": "Marginalia is an independent search engine.",
            "quality": 2.84,
            "format": "html",
            "resultsFromDomain": 5,
        },
        {
            "url": "https://example.com/search",
            "title": "Search Engines",
            "description": "A comparison of search engines.",
            "quality": 2.91,
            "format": "html",
            "resultsFromDomain": 1,
        },
    ],
}


@respx.mock
def test_marginalia_search():
    respx.get("https://api.marginalia.nu/public/search/search%20engines").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MarginaliaClient()
    results = client.search("search engines")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "About Marginalia"
    assert results[0].source == "marginalia"
    assert results[0].url == "https://bottlepy.org/docs/dev/"


@respx.mock
def test_marginalia_search_max_results():
    respx.get("https://api.marginalia.nu/public/search/test").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MarginaliaClient()
    results = client.search("test", max_results=1)
    assert len(results) == 1


@respx.mock
def test_marginalia_search_custom_config():
    respx.get("https://example.com/api/public/search/test").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(marginalia_url="https://example.com/api")
    client = MarginaliaClient(config=config)
    results = client.search("test")
    assert len(results) == 2


@respx.mock
def test_marginalia_search_error():
    respx.get("https://api.marginalia.nu/public/search/test").mock(
        return_value=httpx.Response(500)
    )
    client = MarginaliaClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_marginalia_search_empty():
    respx.get("https://api.marginalia.nu/public/search/test").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = MarginaliaClient()
    results = client.search("test")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_marginalia_async_search():
    respx.get("https://api.marginalia.nu/public/search/search%20engines").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MarginaliaClient()
    results = await client.asearch("search engines")
    assert len(results) == 2
    assert results[0].source == "marginalia"


@respx.mock
def test_marginalia_sends_correct_params():
    respx.get("https://api.marginalia.nu/public/search/test%20query").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MarginaliaClient()
    client.search("test query", max_results=10)
    request = respx.calls[0].request
    assert "count=10" in str(request.url)
