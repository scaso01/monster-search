"""Tests for Meilisearch client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.meilisearch_client import MeilisearchClient
from monster_search.config import Config
from monster_search.models import SearchResult

MEILI_URL = "http://localhost:7700"

SAMPLE_RESULTS = [
    SearchResult(title="Result One", url="https://example.com/one", snippet="First result", source="searxng"),
    SearchResult(title="Result Two", url="https://example.com/two", snippet="Second result", source="searxng"),
]

MOCK_SEARCH_RESPONSE = {
    "hits": [
        {"title": "Result One", "url": "https://example.com/one", "snippet": "First result", "source": "searxng"},
        {"title": "Result Two", "url": "https://example.com/two", "snippet": "Second result", "source": "searxng"},
    ],
    "query": "test",
    "processingTimeMs": 1,
    "limit": 5,
    "offset": 0,
    "estimatedTotalHits": 2,
}


@respx.mock
def test_meilisearch_search():
    respx.post(f"{MEILI_URL}/indexes/search_results/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    client = MeilisearchClient()
    results = client.search("test")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "Result One"
    assert results[0].source == "meilisearch"


@respx.mock
def test_meilisearch_search_max_results():
    respx.post(f"{MEILI_URL}/indexes/search_results/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    client = MeilisearchClient()
    results = client.search("test", max_results=1)
    assert len(results) == 1


@respx.mock
def test_meilisearch_search_empty():
    respx.post(f"{MEILI_URL}/indexes/search_results/search").mock(
        return_value=httpx.Response(200, json={"hits": [], "query": "nothing", "processingTimeMs": 0})
    )
    client = MeilisearchClient()
    results = client.search("nothing")
    assert results == []


@respx.mock
def test_meilisearch_search_error():
    respx.post(f"{MEILI_URL}/indexes/search_results/search").mock(
        return_value=httpx.Response(500)
    )
    client = MeilisearchClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_meilisearch_index_results():
    respx.post(f"{MEILI_URL}/indexes").mock(
        return_value=httpx.Response(202, json={"taskUid": 1})
    )
    respx.post(f"{MEILI_URL}/indexes/search_results/documents").mock(
        return_value=httpx.Response(202, json={"taskUid": 2})
    )
    client = MeilisearchClient()
    client.index_results("test query", SAMPLE_RESULTS, engine="searxng")
    # Verify documents were posted
    doc_request = respx.calls[1].request
    assert doc_request.url.path == "/indexes/search_results/documents"


@respx.mock
def test_meilisearch_index_results_empty():
    """Indexing empty results should be a no-op."""
    client = MeilisearchClient()
    client.index_results("test", [])
    assert len(respx.calls) == 0


@respx.mock
def test_meilisearch_index_already_exists():
    """409 from index creation (already exists) should not raise."""
    respx.post(f"{MEILI_URL}/indexes").mock(
        return_value=httpx.Response(409, json={"message": "already exists"})
    )
    respx.post(f"{MEILI_URL}/indexes/search_results/documents").mock(
        return_value=httpx.Response(202, json={"taskUid": 3})
    )
    client = MeilisearchClient()
    client.index_results("test", SAMPLE_RESULTS)


@respx.mock
def test_meilisearch_health_ok():
    respx.get(f"{MEILI_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": "available"})
    )
    client = MeilisearchClient()
    assert client.health() is True


@respx.mock
def test_meilisearch_health_down():
    respx.get(f"{MEILI_URL}/health").mock(
        return_value=httpx.Response(503)
    )
    client = MeilisearchClient()
    assert client.health() is False


@respx.mock
def test_meilisearch_health_unreachable():
    respx.get(f"{MEILI_URL}/health").mock(side_effect=httpx.ConnectError("refused"))
    client = MeilisearchClient()
    assert client.health() is False


@respx.mock
def test_meilisearch_custom_config():
    custom_url = "http://localhost:9900"
    respx.post(f"{custom_url}/indexes/search_results/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    config = Config(meilisearch_url=custom_url, meilisearch_key="custom-key")
    client = MeilisearchClient(config=config)
    results = client.search("test")
    assert len(results) == 2
    # Verify auth header uses custom key
    request = respx.calls[0].request
    assert request.headers["authorization"] == "Bearer custom-key"


@respx.mock
@pytest.mark.asyncio
async def test_meilisearch_async_search():
    respx.post(f"{MEILI_URL}/indexes/search_results/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_RESPONSE)
    )
    client = MeilisearchClient()
    results = await client.asearch("test")
    assert len(results) == 2
    assert results[0].source == "meilisearch"


@respx.mock
@pytest.mark.asyncio
async def test_meilisearch_async_index():
    respx.post(f"{MEILI_URL}/indexes").mock(
        return_value=httpx.Response(202, json={"taskUid": 1})
    )
    respx.post(f"{MEILI_URL}/indexes/search_results/documents").mock(
        return_value=httpx.Response(202, json={"taskUid": 2})
    )
    client = MeilisearchClient()
    await client.aindex_results("test", SAMPLE_RESULTS, engine="searxng")
    assert len(respx.calls) == 2


@respx.mock
@pytest.mark.asyncio
async def test_meilisearch_async_health():
    respx.get(f"{MEILI_URL}/health").mock(
        return_value=httpx.Response(200, json={"status": "available"})
    )
    client = MeilisearchClient()
    assert await client.ahealth() is True


def test_meilisearch_doc_id_deterministic():
    """Same URL should always produce the same doc ID."""
    client = MeilisearchClient()
    id1 = client._doc_id("https://example.com/test")
    id2 = client._doc_id("https://example.com/test")
    assert id1 == id2
    assert len(id1) == 16


def test_meilisearch_doc_id_unique():
    """Different URLs should produce different doc IDs."""
    client = MeilisearchClient()
    id1 = client._doc_id("https://example.com/a")
    id2 = client._doc_id("https://example.com/b")
    assert id1 != id2
