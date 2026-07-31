from __future__ import annotations

import httpx
import json
import pytest
import respx

from monster_search.clients.perplexica import PerplexicaClient
from monster_search.models import SearchResult

MOCK_PROVIDERS = {
    "providers": [
        {
            "id": "embed-provider-id",
            "name": "Transformers",
            "chatModels": [],
            "embeddingModels": [
                {"name": "all-MiniLM-L6-v2", "key": "Xenova/all-MiniLM-L6-v2"},
            ],
        },
        {
            "id": "test-provider",
            "name": "Test Provider",
            "chatModels": [
                {"name": "qwen3-coder", "displayName": "Qwen3 Coder"},
            ],
            "embeddingModels": [],
        },
    ]
}

MOCK_SEARCH = {
    "message": "Python asyncio is a library for writing concurrent code using async/await syntax.",
    "sources": [
        {
            "url": "https://docs.python.org/3/library/asyncio.html",
            "title": "asyncio docs",
            "content": "asyncio is a library to write concurrent code.",
        },
        {
            "url": "https://realpython.com/async-io-python/",
            "title": "Real Python Tutorial",
            "content": "A complete walkthrough.",
        },
    ],
}


@respx.mock
def test_perplexica_resolves_providers():
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    client = PerplexicaClient()
    chat_id, embed_id = client._resolve_provider_ids()
    assert chat_id == "test-provider"
    assert embed_id == "embed-provider-id"


@respx.mock
def test_perplexica_search():
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH)
    )
    client = PerplexicaClient()
    message, results = client.search("python asyncio")
    assert "asyncio" in message
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "perplexica"
    assert results[0].url == "https://docs.python.org/3/library/asyncio.html"


@respx.mock
def test_perplexica_search_focus_mode():
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH)
    )
    client = PerplexicaClient()
    client.search("python asyncio", focus_mode="academicSearch")
    request = respx.calls[1].request  # second call is the search POST
    body = json.loads(request.content)
    assert body["focusMode"] == "academicSearch"


@respx.mock
def test_perplexica_search_no_sources():
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json={"message": "No results found.", "sources": []})
    )
    client = PerplexicaClient()
    message, results = client.search("gibberish query")
    assert message == "No results found."
    assert results == []


@respx.mock
def test_perplexica_search_message_only():
    """Perplexica sometimes returns message without sources key."""
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json={"message": "Just a message"})
    )
    client = PerplexicaClient()
    message, results = client.search("test")
    assert message == "Just a message"
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_perplexica_async_search():
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH)
    )
    client = PerplexicaClient()
    message, results = await client.asearch("python asyncio")
    assert "asyncio" in message
    assert len(results) == 2


@respx.mock
def test_perplexica_model_override():
    """Model override selects matching provider and uses override model."""
    providers_with_two = {
        "providers": [
            {
                "id": "embed-provider-id",
                "chatModels": [],
                "embeddingModels": [{"key": "Xenova/all-MiniLM-L6-v2"}],
            },
            {
                "id": "provider-a",
                "chatModels": [{"name": "default-model"}],
                "embeddingModels": [],
            },
            {
                "id": "provider-b",
                "chatModels": [{"name": "override-model"}],
                "embeddingModels": [],
            },
        ]
    }
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=providers_with_two)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH)
    )
    client = PerplexicaClient()
    client.search("test", model="override-model")
    request = respx.calls[1].request
    body = json.loads(request.content)
    assert body["chatModel"]["providerId"] == "provider-b"
    assert body["chatModel"]["name"] == "override-model"


MOCK_SEARCH_METADATA = {
    "message": "Sources with nested metadata.",
    "sources": [
        {
            "content": "asyncio is a library to write concurrent code.",
            "metadata": {
                "url": "https://docs.python.org/3/library/asyncio.html",
                "title": "asyncio docs",
            },
        },
        {
            "content": "A complete walkthrough.",
            "metadata": {
                "url": "https://realpython.com/async-io-python/",
                "title": "Real Python Tutorial",
            },
        },
    ],
}


@respx.mock
def test_perplexica_search_metadata_sources():
    """Perplexica returns sources with title/url nested under metadata."""
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH_METADATA)
    )
    client = PerplexicaClient()
    message, results = client.search("python asyncio")
    assert len(results) == 2
    assert results[0].url == "https://docs.python.org/3/library/asyncio.html"
    assert results[0].title == "asyncio docs"
    assert results[0].snippet == "asyncio is a library to write concurrent code."
    assert results[1].url == "https://realpython.com/async-io-python/"
    assert results[1].title == "Real Python Tutorial"


@respx.mock
def test_perplexica_model_override_skips_cache():
    """Override should not cache, next call without override uses default."""
    respx.get("http://localhost:3001/api/providers").mock(
        return_value=httpx.Response(200, json=MOCK_PROVIDERS)
    )
    respx.post("http://localhost:3001/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARCH)
    )
    client = PerplexicaClient()
    # First call with override
    client.search("test", model="override-model")
    # Override should NOT be cached — provider IDs should still be None
    assert client._chat_provider_id is None
    # Second call without override resolves and caches normally
    client.search("test")
    assert client._chat_provider_id is not None
