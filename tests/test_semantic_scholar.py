from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.semantic_scholar import (
    SemanticScholarClient,
    _NO_KEY_MSG,
    _RATE_LIMITED_WITH_KEY_MSG,
)
from monster_search.config import Config
from monster_search.models import SearchResult

# A dummy key used in tests that exercise normal request paths.
_TEST_KEY = "test-key-123"
_KEYED_CONFIG = Config(semantic_scholar_api_key=_TEST_KEY)

MOCK_RESPONSE = {
    "total": 2,
    "offset": 0,
    "next": 2,
    "data": [
        {
            "paperId": "abc123",
            "title": "Attention Is All You Need",
            "abstract": "The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.",
            "url": "https://www.semanticscholar.org/paper/abc123",
            "year": 2017,
            "citationCount": 95000,
            "authors": [
                {"authorId": "1", "name": "Ashish Vaswani"},
                {"authorId": "2", "name": "Noam Shazeer"},
            ],
            "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762"},
            "publicationDate": "2017-06-12",
        },
        {
            "paperId": "def456",
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "abstract": "We introduce a new language representation model called BERT.",
            "url": "https://www.semanticscholar.org/paper/def456",
            "year": 2018,
            "citationCount": 72000,
            "authors": [
                {"authorId": "3", "name": "Jacob Devlin"},
            ],
            "openAccessPdf": None,
            "publicationDate": "2018-10-11",
        },
    ],
}


# ---------------------------------------------------------------------------
# No-key guard tests
# ---------------------------------------------------------------------------

def test_semantic_scholar_no_key_raises_immediately(monkeypatch):
    """Without an API key, search() raises RuntimeError before any HTTP call."""
    # Ensure the env var is absent for this test regardless of .env contents.
    monkeypatch.delenv("MONSTER_SEMANTIC_SCHOLAR_API_KEY", raising=False)
    config = Config(semantic_scholar_api_key="")
    client = SemanticScholarClient(config=config)
    with pytest.raises(RuntimeError) as exc_info:
        client.search("transformer attention")
    msg = str(exc_info.value)
    assert "https://www.semanticscholar.org/product/api#api-key" in msg
    assert "MONSTER_SEMANTIC_SCHOLAR_API_KEY" in msg


@pytest.mark.asyncio
async def test_semantic_scholar_async_no_key_raises_immediately(monkeypatch):
    """Without an API key, asearch() raises RuntimeError before any HTTP call."""
    monkeypatch.delenv("MONSTER_SEMANTIC_SCHOLAR_API_KEY", raising=False)
    config = Config(semantic_scholar_api_key="")
    client = SemanticScholarClient(config=config)
    with pytest.raises(RuntimeError) as exc_info:
        await client.asearch("transformer attention")
    assert "MONSTER_SEMANTIC_SCHOLAR_API_KEY" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Normal request paths (key present)
# ---------------------------------------------------------------------------

@respx.mock
def test_semantic_scholar_search():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    results = client.search("transformers")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "Attention Is All You Need"
    assert results[0].source == "semantic_scholar"
    assert results[0].url == "https://www.semanticscholar.org/paper/abc123"
    assert results[0].published == "2017-06-12"
    assert results[0].score == 95000.0


@respx.mock
def test_semantic_scholar_search_max_results():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    results = client.search("transformers", max_results=1)
    assert len(results) == 1


@respx.mock
def test_semantic_scholar_search_custom_config():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(semantic_scholar_timeout=60, semantic_scholar_api_key=_TEST_KEY)
    client = SemanticScholarClient(config=config)
    results = client.search("transformers")
    assert len(results) == 2


@respx.mock
def test_semantic_scholar_search_with_api_key():
    """API key is sent as x-api-key header."""
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    client.search("transformers")
    request = respx.calls[0].request
    assert request.headers["x-api-key"] == _TEST_KEY


@respx.mock
def test_semantic_scholar_search_error():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(500)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    with pytest.raises(httpx.HTTPStatusError):
        client.search("transformers")


@respx.mock
def test_semantic_scholar_search_empty():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "offset": 0, "data": []})
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    results = client.search("transformers")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_semantic_scholar_async_search():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    results = await client.asearch("transformers")
    assert len(results) == 2
    assert results[0].source == "semantic_scholar"
    assert results[1].title == "BERT: Pre-training of Deep Bidirectional Transformers"


@respx.mock
def test_semantic_scholar_sends_correct_params():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    client.search("neural networks", max_results=10)
    request = respx.calls[0].request
    assert "query=neural+networks" in str(request.url) or "query=neural%20networks" in str(request.url)
    assert "limit=10" in str(request.url)
    assert "fields=" in str(request.url)


@respx.mock
def test_semantic_scholar_abstract_truncation():
    long_abstract = "A" * 600
    data = {
        "total": 1,
        "offset": 0,
        "data": [
            {
                "paperId": "xyz",
                "title": "Long Abstract Paper",
                "abstract": long_abstract,
                "url": "https://www.semanticscholar.org/paper/xyz",
                "year": 2024,
                "citationCount": 5,
                "authors": [],
                "openAccessPdf": None,
                "publicationDate": "2024-01-01",
            },
        ],
    }
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json=data)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    results = client.search("test")
    assert len(results[0].snippet) == 500


# ---------------------------------------------------------------------------
# 429 handling with key set
# ---------------------------------------------------------------------------

@respx.mock
def test_semantic_scholar_429_with_key_exhausted_raises_runtime_error():
    """Exhausted 429 retries with key set raises RuntimeError with clear message."""
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(429)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    with pytest.raises(RuntimeError) as exc_info:
        client.search("transformer attention")
    assert "rate-limited" in str(exc_info.value).lower() or "rate limited" in str(exc_info.value).lower()
    assert "API key" in str(exc_info.value)


@respx.mock
@pytest.mark.asyncio
async def test_semantic_scholar_async_429_with_key_exhausted_raises_runtime_error():
    """Async exhausted 429 retries with key raises RuntimeError."""
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(429)
    )
    client = SemanticScholarClient(config=_KEYED_CONFIG)
    with pytest.raises(RuntimeError) as exc_info:
        await client.asearch("transformer attention")
    assert "API key" in str(exc_info.value)
