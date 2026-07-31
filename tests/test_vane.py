"""Tests for the Vane AI search client (Perplexica-compatible API)."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.vane import VaneClient
from monster_search.models import SearchResult


PROVIDERS = {
    "providers": [
        {
            "id": "chat-provider-uuid",
            "chatModels": [{"key": "qwen3-coder", "name": "Qwen3 Coder"}],
        },
        {
            "id": "embed-provider-uuid",
            "embeddingModels": [{"key": "Xenova/all-MiniLM-L6-v2"}],
        },
    ]
}

SEARCH_RESPONSE = {
    "message": "Tokio is the de facto asynchronous runtime for Rust.",
    "sources": [
        {
            "content": "Tokio provides an event-driven, non-blocking I/O platform.",
            "metadata": {"title": "Tokio", "url": "https://tokio.rs"},
        },
        {
            "content": "async-std mirrors the standard library API.",
            "metadata": {"title": "async-std", "url": "https://async.rs"},
        },
    ],
}


def _mock_provider_and_search(search_json=SEARCH_RESPONSE):
    respx.get("http://localhost:3004/api/providers").mock(
        return_value=httpx.Response(200, json=PROVIDERS)
    )
    respx.post("http://localhost:3004/api/search").mock(
        return_value=httpx.Response(200, json=search_json)
    )


# --- provider resolution ------------------------------------------------


def test_resolve_provider_ids_picks_first_of_each_kind():
    """Chat and embedding IDs come from the first provider offering each."""
    client = VaneClient()
    chat_id, embed_id, model_key = client._resolve_provider_ids_from_data(
        PROVIDERS["providers"]
    )

    assert chat_id == "chat-provider-uuid"
    assert embed_id == "embed-provider-uuid"
    assert model_key == "qwen3-coder"


def test_resolve_provider_ids_falls_back_to_model_name():
    """A chat model with no `key` falls back to its `name`."""
    providers = [
        {"id": "c", "chatModels": [{"name": "Only A Name"}]},
        {"id": "e", "embeddingModels": [{"key": "emb"}]},
    ]
    _, _, model_key = VaneClient()._resolve_provider_ids_from_data(providers)

    assert model_key == "Only A Name"


def test_resolve_provider_ids_single_provider_serves_both():
    """One provider offering both kinds is used for both IDs."""
    providers = [
        {
            "id": "both",
            "chatModels": [{"key": "m"}],
            "embeddingModels": [{"key": "e"}],
        }
    ]
    chat_id, embed_id, _ = VaneClient()._resolve_provider_ids_from_data(providers)

    assert chat_id == "both"
    assert embed_id == "both"


def test_resolve_provider_ids_raises_without_chat_model():
    """No chat provider is unrecoverable, so it must raise, not return junk."""
    providers = [{"id": "e", "embeddingModels": [{"key": "emb"}]}]
    with pytest.raises(RuntimeError, match="Could not resolve Vane provider IDs"):
        VaneClient()._resolve_provider_ids_from_data(providers)


def test_resolve_provider_ids_raises_without_embedding_model():
    """Same for a missing embedding provider."""
    providers = [{"id": "c", "chatModels": [{"key": "m"}]}]
    with pytest.raises(RuntimeError, match="Could not resolve Vane provider IDs"):
        VaneClient()._resolve_provider_ids_from_data(providers)


def test_resolve_provider_ids_raises_on_empty_provider_list():
    """An empty list is the shape a freshly-built container returns."""
    with pytest.raises(RuntimeError):
        VaneClient()._resolve_provider_ids_from_data([])


# --- payload ------------------------------------------------------------


def test_build_payload_shape():
    """Payload carries the resolved IDs and the fixed embedding key."""
    client = VaneClient()
    client._model_key = "qwen3-coder"
    payload = client._build_payload("rust async", "chat-id", "embed-id", "webSearch")

    assert payload["query"] == "rust async"
    assert payload["chatModel"] == {"providerId": "chat-id", "key": "qwen3-coder"}
    assert payload["embeddingModel"]["providerId"] == "embed-id"
    assert payload["focusMode"] == "webSearch"
    assert payload["history"] == []


# --- response parsing ---------------------------------------------------


def test_parse_results_reads_nested_metadata():
    """Title and URL live under `metadata`, snippet under `content`."""
    message, results = VaneClient()._parse_results(SEARCH_RESPONSE)

    assert message.startswith("Tokio is the de facto")
    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].title == "Tokio"
    assert results[0].url == "https://tokio.rs"
    assert results[0].snippet.startswith("Tokio provides")
    assert results[0].source == "vane"


def test_parse_results_falls_back_to_flat_source():
    """Older payloads put title/url on the source itself, not in metadata."""
    flat = {
        "message": "answer",
        "sources": [{"title": "Flat", "url": "https://flat.example", "content": "c"}],
    }
    _, results = VaneClient()._parse_results(flat)

    assert results[0].title == "Flat"
    assert results[0].url == "https://flat.example"


def test_parse_results_no_sources():
    """An answer with no sources yields a message and an empty list."""
    message, results = VaneClient()._parse_results({"message": "just prose"})

    assert message == "just prose"
    assert results == []


def test_parse_results_empty_payload():
    """A completely empty payload must not raise."""
    message, results = VaneClient()._parse_results({})

    assert message == ""
    assert results == []


# --- end to end ---------------------------------------------------------


@respx.mock
def test_search_resolves_then_queries():
    """A search fetches providers, then posts to /api/search."""
    _mock_provider_and_search()
    message, results = VaneClient().search("rust async")

    assert len(results) == 2
    assert message.startswith("Tokio")
    assert respx.calls[0].request.url.path == "/api/providers"
    assert respx.calls[1].request.url.path == "/api/search"


@respx.mock
def test_search_caches_provider_ids():
    """Provider IDs are fetched once per client, not once per search."""
    _mock_provider_and_search()
    client = VaneClient()
    client.search("first")
    client.search("second")

    provider_calls = [c for c in respx.calls if c.request.url.path == "/api/providers"]
    assert len(provider_calls) == 1


@respx.mock
def test_search_passes_focus_mode_through():
    """A non-default focus mode reaches the request body."""
    _mock_provider_and_search()
    VaneClient().search("papers on attention", focus_mode="academicSearch")

    search_call = [c for c in respx.calls if c.request.url.path == "/api/search"][0]
    import json

    assert json.loads(search_call.request.content)["focusMode"] == "academicSearch"


@respx.mock
def test_search_raises_when_providers_unavailable():
    """A 500 on provider resolution is surfaced, not silently skipped."""
    respx.get("http://localhost:3004/api/providers").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        VaneClient().search("anything")


@respx.mock
def test_search_raises_on_search_error():
    """A 500 on the search call is surfaced too."""
    respx.get("http://localhost:3004/api/providers").mock(
        return_value=httpx.Response(200, json=PROVIDERS)
    )
    respx.post("http://localhost:3004/api/search").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        VaneClient().search("anything")


@respx.mock
@pytest.mark.asyncio
async def test_async_search():
    """Async path returns the same shape as the sync one."""
    _mock_provider_and_search()
    message, results = await VaneClient().asearch("rust async")

    assert len(results) == 2
    assert results[0].url == "https://tokio.rs"
    assert message.startswith("Tokio")


@respx.mock
@pytest.mark.asyncio
async def test_async_search_caches_provider_ids():
    """Caching applies on the async path as well."""
    _mock_provider_and_search()
    client = VaneClient()
    await client.asearch("first")
    await client.asearch("second")

    provider_calls = [c for c in respx.calls if c.request.url.path == "/api/providers"]
    assert len(provider_calls) == 1
