"""Tests for HuggingFace Hub model search client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.huggingface import HuggingFaceClient
from monster_search.models import SearchResult


HF_RESPONSE = [
    {
        "modelId": "meta-llama/Llama-3-8B",
        "id": "meta-llama/Llama-3-8B",
        "pipeline_tag": "text-generation",
        "tags": ["pytorch", "llama", "text-generation", "en"],
        "downloads": 5000000,
        "likes": 12000,
    },
    {
        "modelId": "openai/whisper-large-v3",
        "id": "openai/whisper-large-v3",
        "pipeline_tag": "automatic-speech-recognition",
        "tags": ["pytorch", "whisper", "asr"],
        "downloads": 2000000,
        "likes": 8000,
    },
]

HF_EMPTY: list[dict] = []


@respx.mock
def test_huggingface_search():
    """Basic search returns SearchResults with correct fields."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=HF_RESPONSE)
    )
    client = HuggingFaceClient()
    results = client.search("llama", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "huggingface"
    assert results[0].title == "meta-llama/Llama-3-8B"
    assert "huggingface.co/meta-llama/Llama-3-8B" in results[0].url
    assert results[1].title == "openai/whisper-large-v3"
    assert "huggingface.co/openai/whisper-large-v3" in results[1].url


@respx.mock
def test_huggingface_empty():
    """Empty array returns empty list."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=HF_EMPTY)
    )
    client = HuggingFaceClient()
    results = client.search("xyznonexistent123")
    assert results == []


@respx.mock
def test_huggingface_error():
    """HTTP error raises HTTPStatusError."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(500)
    )
    client = HuggingFaceClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_huggingface_timeout():
    """Timeout raises TimeoutException."""
    respx.get("https://huggingface.co/api/models").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = HuggingFaceClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_huggingface_sends_correct_params():
    """Verify search and limit params sent to API."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=HF_EMPTY)
    )
    client = HuggingFaceClient()
    client.search("llama 3", max_results=10)

    request = respx.calls[0].request
    url_str = str(request.url)
    assert "search=llama" in url_str
    assert "limit=10" in url_str


@respx.mock
def test_huggingface_snippet_format():
    """Verify snippet contains pipeline_tag, tags, and download/like counts."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=HF_RESPONSE)
    )
    client = HuggingFaceClient()
    results = client.search("llama", max_results=5)

    snippet = results[0].snippet
    # pipeline_tag
    assert "text-generation" in snippet
    # tags (first 5)
    assert "pytorch" in snippet
    assert "llama" in snippet
    # download count formatted with commas
    assert "5,000,000 downloads" in snippet
    # likes count
    assert "12,000 likes" in snippet


@respx.mock
async def test_huggingface_async_search():
    """Async search works and returns correct results."""
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=HF_RESPONSE)
    )
    client = HuggingFaceClient()
    results = await client.asearch("llama", max_results=5)

    assert len(results) == 2
    assert results[0].source == "huggingface"
    assert results[0].title == "meta-llama/Llama-3-8B"
