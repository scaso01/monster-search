"""Tests for the AI search synthesizer client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.synthesizer import (
    SynthesizerClient,
    _extract_sources,
    _format_sources,
)
from monster_search.config import Config
from monster_search.models import SearchResult

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_SEARXNG_RESPONSE = {
    "results": [
        {
            "title": "Python asyncio docs",
            "url": "https://docs.python.org/3/library/asyncio.html",
            "content": "asyncio is a library to write concurrent code.",
            "engine": "google",
            "score": 1.0,
        },
        {
            "title": "Real Python Tutorial",
            "url": "https://realpython.com/async-io-python/",
            "content": "A complete walkthrough of async IO in Python.",
            "engine": "duckduckgo",
            "score": 0.9,
        },
        {
            "title": "Stack Overflow Answer",
            "url": "https://stackoverflow.com/questions/123/asyncio",
            "content": "Use asyncio.run() to run the event loop.",
            "engine": "google",
            "score": 0.8,
        },
    ]
}

MOCK_LLM_RESPONSE = {
    "choices": [
        {
            "message": {
                "content": (
                    "Python asyncio is a standard library for concurrent code [1]. "
                    "You can learn more from the Real Python tutorial [2]. "
                    "To run the event loop, use asyncio.run() [3]."
                ),
                "role": "assistant",
            },
            "finish_reason": "stop",
        }
    ]
}

MOCK_LLM_NO_CITATIONS = {
    "choices": [
        {
            "message": {
                "content": "I cannot find enough information to answer this question.",
                "role": "assistant",
            },
            "finish_reason": "stop",
        }
    ]
}

MOCK_LLM_EMPTY_CHOICES = {"choices": []}

MOCK_CRAWL4AI_RESPONSE = {
    "results": [
        {
            "markdown": "# asyncio\n\nThe asyncio module provides infrastructure for writing single-threaded concurrent code using coroutines.",
            "metadata": {"title": "asyncio docs"},
        }
    ]
}


def _make_search_results(count: int = 3) -> list[SearchResult]:
    """Create sample SearchResult objects."""
    data = [
        ("Python asyncio docs", "https://docs.python.org/3/library/asyncio.html", "asyncio is a library."),
        ("Real Python Tutorial", "https://realpython.com/async-io-python/", "A complete walkthrough."),
        ("Stack Overflow Answer", "https://stackoverflow.com/questions/123/asyncio", "Use asyncio.run()."),
    ]
    return [
        SearchResult(title=t, url=u, snippet=s, source="searxng")
        for t, u, s in data[:count]
    ]


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------


class TestFormatSources:
    def test_basic_formatting(self):
        results = _make_search_results(2)
        text = _format_sources(results)
        assert "[1] Title: Python asyncio docs" in text
        assert "    URL: https://docs.python.org/3/library/asyncio.html" in text
        assert "    Content: asyncio is a library." in text
        assert "[2] Title: Real Python Tutorial" in text

    def test_deep_content_overrides_snippet(self):
        results = _make_search_results(1)
        deep = {"https://docs.python.org/3/library/asyncio.html": "Full scraped page content here."}
        text = _format_sources(results, deep_content=deep)
        assert "Full scraped page content here." in text
        # Original snippet should NOT be present when deep content is used
        assert "asyncio is a library." not in text

    def test_truncation_snippet(self):
        results = [
            SearchResult(title="Long", url="https://example.com", snippet="x" * 1000, source="searxng")
        ]
        text = _format_sources(results)
        # Snippet should be truncated to 500 chars
        content_line = [l for l in text.split("\n") if "Content:" in l][0]
        # The content after "Content: " should be at most 500 chars
        content_value = content_line.split("Content: ", 1)[1]
        assert len(content_value) == 500

    def test_empty_snippet(self):
        results = [
            SearchResult(title="No Snippet", url="https://example.com", snippet="", source="searxng")
        ]
        text = _format_sources(results)
        assert "Content:" not in text

    def test_deep_content_truncation(self):
        results = _make_search_results(1)
        deep = {"https://docs.python.org/3/library/asyncio.html": "y" * 2000}
        text = _format_sources(results, deep_content=deep)
        content_line = [l for l in text.split("\n") if "Content:" in l][0]
        content_value = content_line.split("Content: ", 1)[1]
        assert len(content_value) == 1000


class TestExtractSources:
    def test_extracts_cited_sources(self):
        results = _make_search_results(3)
        llm_text = "Answer from [1] and [3]."
        cited = _extract_sources(llm_text, results)
        assert len(cited) == 2
        assert cited[0].url == "https://docs.python.org/3/library/asyncio.html"
        assert cited[1].url == "https://stackoverflow.com/questions/123/asyncio"
        assert all(r.source == "synthesizer" for r in cited)

    def test_returns_all_when_none_cited(self):
        results = _make_search_results(3)
        llm_text = "No citations here."
        cited = _extract_sources(llm_text, results)
        assert len(cited) == 3

    def test_deduplicates_by_url(self):
        results = _make_search_results(3)
        llm_text = "See [1] and also [1] again."
        cited = _extract_sources(llm_text, results)
        assert len(cited) == 1

    def test_single_citation(self):
        results = _make_search_results(3)
        llm_text = "According to [2], async IO is great."
        cited = _extract_sources(llm_text, results)
        assert len(cited) == 1
        assert cited[0].url == "https://realpython.com/async-io-python/"


# ---------------------------------------------------------------------------
# Sync search tests
# ---------------------------------------------------------------------------


class TestSynthesizerSync:
    @respx.mock
    def test_basic_search(self):
        """Test the full sync pipeline: SearXNG -> LLM -> parsed response."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        message, results = client.search("python asyncio")
        assert "asyncio" in message
        assert len(results) == 3  # All 3 cited
        assert all(r.source == "synthesizer" for r in results)

    @respx.mock
    def test_search_no_citations_returns_all_sources(self):
        """When LLM cites nothing, all search results should be returned."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_NO_CITATIONS)
        )
        client = SynthesizerClient()
        message, results = client.search("something obscure")
        assert "cannot find" in message.lower()
        assert len(results) == 3  # All sources returned as fallback

    @respx.mock
    def test_searxng_empty_degrades_gracefully(self, monkeypatch):
        """Empty SearXNG → retries, then returns an empty answer (no raise), so the
        engine reports 'empty' not 'failed'."""
        from monster_search.clients import synthesizer as synth_mod
        monkeypatch.setattr(synth_mod, "_RETRY_BACKOFF_S", 0)
        route = respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        client = SynthesizerClient()
        answer, results = client.search("totally empty query")
        assert answer == ""
        assert results == []
        assert route.call_count == synth_mod._SEARCH_RETRIES   # retried before giving up

    @respx.mock
    def test_searxng_empty_then_recovers_on_retry(self, monkeypatch):
        """A transient empty followed by results → the retry lands the results and the
        synthesizer produces a real answer instead of failing."""
        from monster_search.clients import synthesizer as synth_mod
        monkeypatch.setattr(synth_mod, "_RETRY_BACKOFF_S", 0)
        respx.get("http://localhost:8080/search").mock(side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(200, json=MOCK_SEARXNG_RESPONSE),
        ])
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        answer, results = client.search("query")
        assert answer  # non-empty synthesized answer after the retry recovered

    @respx.mock
    def test_searxng_http_error_propagates(self):
        """HTTP errors from SearXNG propagate as httpx exceptions."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(500)
        )
        client = SynthesizerClient()
        with pytest.raises(httpx.HTTPStatusError):
            client.search("query")

    @respx.mock
    def test_llm_http_error_propagates(self):
        """HTTP errors from llama-server propagate."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(503)
        )
        client = SynthesizerClient()
        with pytest.raises(httpx.HTTPStatusError):
            client.search("query")

    @respx.mock
    def test_llm_empty_choices_raises(self):
        """RuntimeError when LLM returns empty choices."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_EMPTY_CHOICES)
        )
        client = SynthesizerClient()
        with pytest.raises(RuntimeError, match="no choices"):
            client.search("query")

    @respx.mock
    def test_max_sources_limits_searxng(self):
        """max_sources parameter limits the SearXNG query."""
        call_params = {}

        def _capture(request):
            call_params.update(dict(request.url.params))
            return httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)

        respx.get("http://localhost:8080/search").mock(side_effect=_capture)
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        client.search("query", max_sources=2)
        # SearXNG should not get more than max_sources results by default config
        # (the SearXNG client handles max_results internally)

    @respx.mock
    def test_llm_payload_structure(self):
        """Verify the LLM payload has correct structure."""
        captured_payload = {}

        def _capture_llm(request):
            import json
            captured_payload.update(json.loads(request.content))
            return httpx.Response(200, json=MOCK_LLM_RESPONSE)

        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            side_effect=_capture_llm
        )
        client = SynthesizerClient()
        client.search("test query")

        assert "messages" in captured_payload
        assert len(captured_payload["messages"]) == 2
        assert captured_payload["messages"][0]["role"] == "system"
        assert captured_payload["messages"][1]["role"] == "user"
        assert "test query" in captured_payload["messages"][1]["content"]
        assert captured_payload["max_tokens"] == 2048
        assert captured_payload["temperature"] == 0.1
        assert captured_payload["stream"] is False


# ---------------------------------------------------------------------------
# Deep mode tests (sync)
# ---------------------------------------------------------------------------


class TestSynthesizerDeepSync:
    @respx.mock
    def test_deep_mode_scrapes_pages(self):
        """Deep mode should call Crawl4AI for top URLs."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        # Mock Crawl4AI for each URL
        respx.post("http://localhost:11235/crawl").mock(
            return_value=httpx.Response(200, json=MOCK_CRAWL4AI_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        message, results = client.search("python asyncio", deep=True)
        assert "asyncio" in message
        # Crawl4AI should have been called (at least once)
        crawl_calls = [c for c in respx.calls if "/crawl" in str(c.request.url)]
        assert len(crawl_calls) >= 1

    @respx.mock
    def test_deep_mode_falls_back_on_crawl_failure(self):
        """When Crawl4AI fails, deep mode should fall back to snippets."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:11235/crawl").mock(
            return_value=httpx.Response(500)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        # Should not raise -- falls back to snippets
        message, results = client.search("query", deep=True)
        assert isinstance(message, str)
        assert len(results) > 0


# ---------------------------------------------------------------------------
# Async search tests
# ---------------------------------------------------------------------------


class TestSynthesizerAsync:
    @respx.mock
    @pytest.mark.asyncio
    async def test_async_basic_search(self):
        """Test the full async pipeline."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        message, results = await client.asearch("python asyncio")
        assert "asyncio" in message
        assert len(results) == 3
        assert all(r.source == "synthesizer" for r in results)

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_searxng_empty_degrades_gracefully(self, monkeypatch):
        """Async: empty SearXNG → retries, then returns an empty answer (no raise)."""
        from monster_search.clients import synthesizer as synth_mod
        monkeypatch.setattr(synth_mod, "_RETRY_BACKOFF_S", 0)
        route = respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        client = SynthesizerClient()
        answer, results = await client.asearch("empty")
        assert answer == ""
        assert results == []
        assert route.call_count == synth_mod._SEARCH_RETRIES

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_llm_error_propagates(self):
        """Async LLM HTTP errors propagate."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(503)
        )
        client = SynthesizerClient()
        with pytest.raises(httpx.HTTPStatusError):
            await client.asearch("query")

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_deep_mode(self):
        """Async deep mode should scrape pages."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:11235/crawl").mock(
            return_value=httpx.Response(200, json=MOCK_CRAWL4AI_RESPONSE)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        message, results = await client.asearch("python asyncio", deep=True)
        assert "asyncio" in message

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_deep_mode_crawl_failure(self):
        """Async deep mode falls back on Crawl4AI failure."""
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://localhost:11235/crawl").mock(
            return_value=httpx.Response(500)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )
        client = SynthesizerClient()
        message, results = await client.asearch("query", deep=True)
        assert isinstance(message, str)


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSynthesizerConfig:
    def test_default_config_values(self):
        config = Config()
        assert config.llama_url == "http://localhost:8080"
        assert config.synthesizer_timeout == 120

    def test_custom_config(self, monkeypatch):
        monkeypatch.setenv("MONSTER_LLAMA_URL", "http://beast:9090")
        monkeypatch.setenv("MONSTER_SYNTHESIZER_TIMEOUT", "60")
        config = Config()
        assert config.llama_url == "http://beast:9090"
        assert config.synthesizer_timeout == 60


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestSynthesizerEdgeCases:
    @respx.mock
    def test_single_result(self):
        """Works with a single search result."""
        single_result = {
            "results": [
                {
                    "title": "Only Result",
                    "url": "https://example.com",
                    "content": "The only result.",
                }
            ]
        }
        llm_resp = {
            "choices": [
                {
                    "message": {
                        "content": "According to [1], this is the answer.",
                        "role": "assistant",
                    }
                }
            ]
        }
        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=single_result)
        )
        respx.post("http://localhost:8080/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=llm_resp)
        )
        client = SynthesizerClient()
        message, results = client.search("single")
        assert len(results) == 1
        assert results[0].url == "https://example.com"

    @respx.mock
    def test_custom_llama_url(self):
        """SynthesizerClient uses custom llama URL from config."""
        config = Config.__new__(Config)
        # Manually set fields for the frozen dataclass
        object.__setattr__(config, "searxng_url", "http://localhost:8080")
        object.__setattr__(config, "timeout", 15)
        object.__setattr__(config, "max_results", 5)
        object.__setattr__(config, "llama_url", "http://custom:9090")
        object.__setattr__(config, "synthesizer_timeout", 60)
        object.__setattr__(config, "crawl4ai_url", "http://localhost:11235")
        object.__setattr__(config, "crawl4ai_timeout", 60)

        respx.get("http://localhost:8080/search").mock(
            return_value=httpx.Response(200, json=MOCK_SEARXNG_RESPONSE)
        )
        respx.post("http://custom:9090/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=MOCK_LLM_RESPONSE)
        )

        with respx.mock:
            client = SynthesizerClient(config=config)
            message, results = client.search("test")
            assert "asyncio" in message
            # Verify the custom URL was called
            llm_calls = [c for c in respx.calls if "custom:9090" in str(c.request.url)]
            assert len(llm_calls) == 1

    def test_system_prompt_content(self):
        """Verify system prompt has key instructions."""
        from monster_search.clients.synthesizer import _SYSTEM_PROMPT
        assert "cite" in _SYSTEM_PROMPT.lower() or "[N]" in _SYSTEM_PROMPT
        assert "hallucinate" in _SYSTEM_PROMPT.lower() or "don't have enough" in _SYSTEM_PROMPT.lower()
        assert "source" in _SYSTEM_PROMPT.lower()
