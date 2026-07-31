from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.mwmbl import MwmblClient
from monster_search.config import Config
from monster_search.models import SearchResult

# Mwmbl returns a JSON LIST (not a {results: [...]} object); title/extract are
# token lists of {value, is_bold} that the client flattens to plain text.
MOCK_RESPONSE = [
    {
        "url": "https://bottlepy.org/docs/dev/",
        "title": [
            {"value": "About ", "is_bold": False},
            {"value": "Mwmbl", "is_bold": True},
        ],
        "extract": [
            {"value": "Mwmbl is an independent ", "is_bold": False},
            {"value": "search", "is_bold": True},
            {"value": " engine.", "is_bold": False},
        ],
        "score": 1.0,
    },
    {
        "url": "https://example.com/search",
        "title": [{"value": "Search Engines", "is_bold": False}],
        "extract": [{"value": "A comparison of search engines.", "is_bold": False}],
        "score": 0.9,
    },
]


@respx.mock
def test_mwmbl_search():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MwmblClient()
    results = client.search("search engines")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "About Mwmbl"
    assert results[0].snippet == "Mwmbl is an independent search engine."
    assert results[0].source == "mwmbl"
    assert results[0].url == "https://bottlepy.org/docs/dev/"


@respx.mock
def test_mwmbl_search_max_results():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MwmblClient()
    results = client.search("test", max_results=1)
    assert len(results) == 1


@respx.mock
def test_mwmbl_search_custom_config():
    respx.get("https://example.com/api/api/v1/search/").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(mwmbl_url="https://example.com/api")
    client = MwmblClient(config=config)
    results = client.search("test")
    assert len(results) == 2


@respx.mock
def test_mwmbl_search_error():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(500)
    )
    client = MwmblClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_mwmbl_search_empty():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = MwmblClient()
    results = client.search("test")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_mwmbl_async_search():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MwmblClient()
    results = await client.asearch("search engines")
    assert len(results) == 2
    assert results[0].source == "mwmbl"


@respx.mock
def test_mwmbl_sends_correct_params():
    respx.get("https://api.mwmbl.org/api/v1/search/").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = MwmblClient()
    client.search("test query", max_results=10)
    request = respx.calls.last.request
    assert request.url.params["s"] == "test query"
