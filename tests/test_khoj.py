"""Tests for the Khoj AI chat/search client."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from monster_search.clients.khoj import KhojClient
from monster_search.models import SearchResult


ORGANIC_RESPONSE = {
    "response": "Rust async is built on futures and an executor.",
    "references": {
        "onlineContext": {
            "rust async runtime": {
                "organic": [
                    {
                        "title": "Tokio",
                        "link": "https://tokio.rs",
                        "description": "An asynchronous runtime for Rust.",
                    },
                    {
                        "title": "async-book",
                        "link": "https://rust-lang.github.io/async-book/",
                        "description": "The async Rust book.",
                    },
                ]
            }
        }
    },
}

LEGACY_RESPONSE = {
    "response": "Here is what I found.",
    "references": {
        "onlineContext": {
            "https://example.com/a": "A snippet as a bare string.",
            "https://example.com/b": {"snippet": "A snippet in a dict."},
            "https://example.com/c": {"content": "Content key instead."},
        }
    },
}


# --- query preparation --------------------------------------------------


def test_prepare_query_adds_online_prefix():
    """The /online prefix is what skips Khoj's tool-selection LLM call."""
    assert KhojClient._prepare_query("rust async") == "/online rust async"


def test_prepare_query_respects_an_explicit_command():
    """A user-supplied command is left alone."""
    assert KhojClient._prepare_query("/notes my meeting") == "/notes my meeting"


def test_prepare_query_detects_command_after_whitespace():
    """Leading whitespace must not hide an explicit command."""
    assert KhojClient._prepare_query("  /notes x") == "  /notes x"


# --- response parsing ---------------------------------------------------


def test_parse_organic_sources():
    """The current format nests results under a subquery's `organic` list."""
    message, results = KhojClient()._parse_response(ORGANIC_RESPONSE)

    assert message.startswith("Rust async is built on")
    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].title == "Tokio"
    assert results[0].url == "https://tokio.rs"
    assert results[0].snippet == "An asynchronous runtime for Rust."
    assert results[0].source == "khoj"


def test_parse_legacy_url_keyed_sources():
    """The older format maps URLs directly to snippet strings or dicts."""
    _, results = KhojClient()._parse_response(LEGACY_RESPONSE)

    by_url = {r.url: r for r in results}
    assert set(by_url) == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }
    assert by_url["https://example.com/a"].snippet == "A snippet as a bare string."
    assert by_url["https://example.com/b"].snippet == "A snippet in a dict."
    assert by_url["https://example.com/c"].snippet == "Content key instead."


def test_parse_deduplicates_across_subqueries():
    """The same link found by two subqueries is returned once."""
    data = {
        "response": "",
        "references": {
            "onlineContext": {
                "q1": {"organic": [{"title": "T", "link": "https://dup.example"}]},
                "q2": {"organic": [{"title": "T again", "link": "https://dup.example"}]},
            }
        },
    }
    _, results = KhojClient()._parse_response(data)

    assert len(results) == 1


def test_parse_skips_entries_with_no_link():
    """An organic item without a link cannot become a result."""
    data = {
        "response": "",
        "references": {
            "onlineContext": {"q": {"organic": [{"title": "No link", "link": ""}]}}
        },
    }
    _, results = KhojClient()._parse_response(data)

    assert results == []


def test_parse_strips_leaked_think_tag():
    """Reasoning models leak a closing </think>; it must not reach the user."""
    message, _ = KhojClient()._parse_response({"response": "</think>  The answer."})

    assert message == "The answer."


def test_parse_reads_top_level_online_context():
    """Streaming and older versions put onlineContext at the top level."""
    data = {
        "response": "x",
        "onlineContext": {"q": {"organic": [{"title": "T", "link": "https://t.example"}]}},
    }
    _, results = KhojClient()._parse_response(data)

    assert len(results) == 1
    assert results[0].url == "https://t.example"


def test_parse_falls_back_to_urls_in_the_message():
    """With no structured context, URLs are salvaged from the prose."""
    data = {"response": "See https://a.example and https://b.example for detail."}
    _, results = KhojClient()._parse_response(data)

    assert [r.url for r in results] == ["https://a.example", "https://b.example"]


def test_parse_url_fallback_deduplicates():
    """A URL repeated in the prose is only returned once."""
    data = {"response": "https://a.example twice: https://a.example"}
    _, results = KhojClient()._parse_response(data)

    assert len(results) == 1


def test_parse_truncates_long_snippets():
    """Snippets are capped at 500 characters."""
    data = {
        "response": "",
        "references": {
            "onlineContext": {
                "q": {
                    "organic": [
                        {"title": "T", "link": "https://t.example", "description": "x" * 900}
                    ]
                }
            }
        },
    }
    _, results = KhojClient()._parse_response(data)

    assert len(results[0].snippet) == 500


def test_parse_empty_payload():
    """An empty payload yields no message and no results."""
    message, results = KhojClient()._parse_response({})

    assert message == ""
    assert results == []


# --- transport ----------------------------------------------------------


@respx.mock
def test_search_posts_expected_body():
    """create_new and stream=False are what make the response parseable."""
    respx.post("http://localhost:42110/api/chat").mock(
        return_value=httpx.Response(200, json=ORGANIC_RESPONSE)
    )
    KhojClient().search("rust async")

    body = json.loads(respx.calls[0].request.content)
    assert body["q"] == "/online rust async"
    assert body["create_new"] is True
    assert body["stream"] is False


@respx.mock
def test_search_returns_message_and_results():
    """End-to-end sync search."""
    respx.post("http://localhost:42110/api/chat").mock(
        return_value=httpx.Response(200, json=ORGANIC_RESPONSE)
    )
    message, results = KhojClient().search("rust async")

    assert message.startswith("Rust async")
    assert len(results) == 2


@respx.mock
def test_search_converts_read_timeout_to_runtime_error():
    """A stalled llama-server should give a readable error, not a raw httpx one."""
    respx.post("http://localhost:42110/api/chat").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(RuntimeError, match="khoj read timeout"):
        KhojClient().search("rust async")


@respx.mock
def test_search_raises_on_http_error():
    """A 500 is surfaced."""
    respx.post("http://localhost:42110/api/chat").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(httpx.HTTPStatusError):
        KhojClient().search("rust async")


@respx.mock
@pytest.mark.asyncio
async def test_async_search():
    """Async path returns the same shape."""
    respx.post("http://localhost:42110/api/chat").mock(
        return_value=httpx.Response(200, json=ORGANIC_RESPONSE)
    )
    message, results = await KhojClient().asearch("rust async")

    assert message.startswith("Rust async")
    assert len(results) == 2


@respx.mock
@pytest.mark.asyncio
async def test_async_search_converts_read_timeout():
    """The async path converts timeouts the same way."""
    respx.post("http://localhost:42110/api/chat").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    with pytest.raises(RuntimeError, match="khoj read timeout"):
        await KhojClient().asearch("rust async")
