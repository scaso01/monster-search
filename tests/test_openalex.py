from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.openalex import OpenAlexClient, _reconstruct_abstract
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_RESPONSE = {
    "meta": {"count": 2, "db_response_time_ms": 15, "page": 1, "per_page": 5},
    "results": [
        {
            "id": "https://openalex.org/W2741809807",
            "doi": "https://doi.org/10.48550/arxiv.1706.03762",
            "title": "Attention Is All You Need",
            "display_name": "Attention Is All You Need",
            "publication_year": 2017,
            "cited_by_count": 95000,
            "open_access": {"is_oa": True},
            "authorships": [
                {"author": {"id": "https://openalex.org/A1", "display_name": "Ashish Vaswani"}},
            ],
            "abstract_inverted_index": {
                "The": [0],
                "dominant": [1],
                "sequence": [2],
                "transduction": [3],
                "models": [4],
                "are": [5],
                "based": [6],
                "on": [7],
                "complex": [8],
                "neural": [9],
                "networks.": [10],
            },
        },
        {
            "id": "https://openalex.org/W2963403868",
            "doi": None,
            "title": "BERT: Pre-training of Deep Bidirectional Transformers",
            "display_name": "BERT: Pre-training of Deep Bidirectional Transformers",
            "publication_year": 2018,
            "cited_by_count": 72000,
            "open_access": {"is_oa": True},
            "authorships": [],
            "abstract_inverted_index": {
                "We": [0],
                "introduce": [1],
                "BERT.": [2],
            },
        },
    ],
}


@respx.mock
def test_openalex_search():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = OpenAlexClient()
    results = client.search("transformers")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "Attention Is All You Need"
    assert results[0].source == "openalex"
    assert results[0].url == "https://doi.org/10.48550/arxiv.1706.03762"
    assert results[0].published == "2017"
    assert results[0].score == 95000.0
    assert results[0].snippet == "The dominant sequence transduction models are based on complex neural networks."


@respx.mock
def test_openalex_search_fallback_url():
    """When doi is None, use the openalex id as URL."""
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = OpenAlexClient()
    results = client.search("transformers")
    assert results[1].url == "https://openalex.org/W2963403868"


@respx.mock
def test_openalex_search_max_results():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = OpenAlexClient()
    results = client.search("transformers", max_results=1)
    assert len(results) == 1


@respx.mock
def test_openalex_search_custom_config():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(openalex_timeout=60)
    client = OpenAlexClient(config=config)
    results = client.search("transformers")
    assert len(results) == 2


@respx.mock
def test_openalex_search_with_mailto():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(openalex_mailto="test@example.com")
    client = OpenAlexClient(config=config)
    client.search("transformers")
    request = respx.calls[0].request
    assert "mailto=test%40example.com" in str(request.url) or "mailto=test@example.com" in str(request.url)


@respx.mock
def test_openalex_search_error():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(500)
    )
    client = OpenAlexClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("transformers")


@respx.mock
def test_openalex_search_empty():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"meta": {"count": 0}, "results": []})
    )
    client = OpenAlexClient()
    results = client.search("transformers")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_openalex_async_search():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = OpenAlexClient()
    results = await client.asearch("transformers")
    assert len(results) == 2
    assert results[0].source == "openalex"
    assert results[1].title == "BERT: Pre-training of Deep Bidirectional Transformers"


@respx.mock
def test_openalex_sends_correct_params():
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = OpenAlexClient()
    client.search("neural networks", max_results=10)
    request = respx.calls[0].request
    url_str = str(request.url)
    assert "per_page=10" in url_str
    assert "search=neural" in url_str
    assert "select=" in url_str


def test_reconstruct_abstract():
    inverted = {"Hello": [0], "world": [1], "this": [2], "is": [3], "a": [4], "test": [5]}
    assert _reconstruct_abstract(inverted) == "Hello world this is a test"


def test_reconstruct_abstract_empty():
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({}) == ""


def test_reconstruct_abstract_truncation():
    # Build an inverted index that produces >500 chars
    words = {f"word{i:04d}x": [i] for i in range(200)}
    result = _reconstruct_abstract(words)
    assert len(result) <= 500
