"""Tests for grep.app public code search client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.grepapp import GrepAppClient
from monster_search.models import SearchResult


GREPAPP_RESPONSE = {
    "facets": {},
    "hits": {
        "hits": [
            {
                "id": "abc123",
                "repo": {"raw": "tokio-rs/tokio"},
                "path": {"raw": "tokio/src/runtime/scheduler/multi_thread/worker.rs"},
                "content": {"snippet": "async fn main() {\n    tokio::spawn(async { });\n}"},
                "language": {"raw": "Rust"},
            },
            {
                "id": "def456",
                "repo": {"raw": "hyperium/hyper"},
                "path": {"raw": "src/server/conn.rs"},
                "content": {"snippet": "pub async fn serve(self) -> Result<()> {"},
                "language": {"raw": "Rust"},
            },
        ],
        "total": 12345,
    },
}

GREPAPP_EMPTY = {"facets": {}, "hits": {"hits": [], "total": 0}}


@respx.mock
def test_grepapp_search():
    """Basic search returns SearchResults."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_RESPONSE)
    )
    client = GrepAppClient()
    results = client.search("async fn main", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "grepapp"
    assert "tokio/src/runtime" in results[0].title
    assert "tokio-rs/tokio" in results[0].url
    assert "async fn main" in results[0].snippet
    assert results[0].category == "Rust"


@respx.mock
def test_grepapp_search_empty():
    """Empty results return empty list."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_EMPTY)
    )
    client = GrepAppClient()
    results = client.search("xyznonexistent123")
    assert results == []


@respx.mock
def test_grepapp_search_error():
    """HTTP error raises HTTPStatusError."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(500)
    )
    client = GrepAppClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_grepapp_search_timeout():
    """Timeout raises TimeoutException."""
    respx.get("https://grep.app/api/search").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = GrepAppClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_grepapp_sends_correct_params():
    """Verify query params sent to API."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_EMPTY)
    )
    client = GrepAppClient()
    client.search("async fn main", max_results=10)

    request = respx.calls[0].request
    assert "q=async" in str(request.url)


@respx.mock
def test_grepapp_sends_user_agent():
    """User-Agent header is included in requests."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_EMPTY)
    )
    client = GrepAppClient()
    client.search("test")

    request = respx.calls[0].request
    assert "monster-search" in request.headers.get("user-agent", "")


@respx.mock
def test_grepapp_custom_user_agent(monkeypatch):
    """MONSTER_GREPAPP_USER_AGENT env var overrides default User-Agent."""
    monkeypatch.setenv("MONSTER_GREPAPP_USER_AGENT", "my-custom-agent/1.0")
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_EMPTY)
    )
    client = GrepAppClient()
    client.search("test")

    request = respx.calls[0].request
    assert request.headers.get("user-agent") == "my-custom-agent/1.0"


@respx.mock
def test_grepapp_429_no_retry_after_raises_runtime_error():
    """429 without Retry-After raises a RuntimeError with VPN rate-limit message."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(429)
    )
    client = GrepAppClient()
    with pytest.raises(RuntimeError) as exc_info:
        client.search("tokio::select")

    msg = str(exc_info.value)
    assert "429" in msg
    assert "VPN" in msg or "rate-limit" in msg.lower()
    assert "MONSTER_GREPAPP_USER_AGENT" in msg


@respx.mock
def test_grepapp_429_with_retry_after_succeeds_on_retry():
    """429 with Retry-After header causes a single retry; success on retry is returned."""
    route = respx.get("https://grep.app/api/search")
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, json=GREPAPP_RESPONSE),
    ]
    client = GrepAppClient()
    results = client.search("async fn main", max_results=5)
    assert len(results) == 2
    assert results[0].source == "grepapp"


@respx.mock
def test_grepapp_429_with_retry_after_still_429_raises_runtime_error():
    """429 with Retry-After header that still returns 429 on retry raises RuntimeError."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(429, headers={"retry-after": "0"})
    )
    client = GrepAppClient()
    with pytest.raises(RuntimeError) as exc_info:
        client.search("tokio::select")

    msg = str(exc_info.value)
    assert "429" in msg


@respx.mock
@pytest.mark.asyncio
async def test_grepapp_async_search():
    """Async search works."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json=GREPAPP_RESPONSE)
    )
    client = GrepAppClient()
    results = await client.asearch("async fn main", max_results=5)

    assert len(results) == 2
    assert results[0].source == "grepapp"


@respx.mock
@pytest.mark.asyncio
async def test_grepapp_async_429_raises_runtime_error():
    """Async 429 without Retry-After raises RuntimeError."""
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(429)
    )
    client = GrepAppClient()
    with pytest.raises(RuntimeError) as exc_info:
        await client.asearch("tokio::select")

    assert "VPN" in str(exc_info.value) or "rate-limit" in str(exc_info.value).lower()
