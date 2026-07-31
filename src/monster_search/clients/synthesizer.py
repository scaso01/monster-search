"""AI search synthesizer — lightweight Perplexica/Vane replacement.

Single LLM call pipeline: SearXNG search -> optional Crawl4AI scrape -> llama-server synthesis.
Replaces two bloated Docker containers (~375 MiB RAM) with ~200 lines of Python.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

from monster_search.clients._pool import get_async_client, get_client
from monster_search.clients.crawl4ai_client import Crawl4AIClient
from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.models import SearchResult

_SYSTEM_PROMPT = """\
You are a search synthesis assistant. Your job is to answer the user's question \
using ONLY the search results provided below. Follow these rules strictly:

1. Synthesize a clear, concise answer from the provided sources.
2. Cite sources using [N] notation (e.g., [1], [2]) matching the numbered source list.
3. Be factual and precise. Do not add information not present in the sources.
4. If the sources do not contain enough information to answer, say \
"I don't have enough information from these sources to fully answer this question." \
and share what partial information is available.
5. Keep your answer under 500 words unless the question requires detailed explanation.
6. Do not repeat the question or the source list in your answer.\
"""

_MAX_SNIPPET_CHARS = 500
_MAX_DEEP_CHARS = 1000
_DEEP_SCRAPE_COUNT = 3

# SearXNG behind a VPN exit intermittently returns 0 results when its few
# VPN-tolerant engines hit a suspension cooldown — often under concurrent load, since
# the synthesizer fires its own SearXNG query alongside the main search. Retry a couple
# of times (the engine mix differs per request, so a brief wait usually lands results)
# before degrading to an empty result instead of a hard failure.
# ponytail: fixed small retry; no backoff library for 3 attempts.
_SEARCH_RETRIES = 3
_RETRY_BACKOFF_S = 1.5


def _format_sources(results: list[SearchResult], *, deep_content: dict[str, str] | None = None) -> str:
    """Format search results as numbered source entries for the LLM prompt."""
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] Title: {r.title}")
        lines.append(f"    URL: {r.url}")
        content = ""
        if deep_content and r.url in deep_content:
            content = deep_content[r.url][:_MAX_DEEP_CHARS]
        elif r.snippet:
            content = r.snippet[:_MAX_SNIPPET_CHARS]
        if content:
            lines.append(f"    Content: {content}")
        lines.append("")
    return "\n".join(lines)


def _extract_sources(llm_text: str, results: list[SearchResult]) -> list[SearchResult]:
    """Return SearchResult list for sources actually cited in the LLM response."""
    cited: list[SearchResult] = []
    seen: set[str] = set()
    for i, r in enumerate(results, 1):
        if f"[{i}]" in llm_text and r.url not in seen:
            seen.add(r.url)
            cited.append(
                SearchResult(
                    title=r.title,
                    url=r.url,
                    snippet=r.snippet[:200] if r.snippet else "",
                    source="synthesizer",
                )
            )
    # If LLM cited nothing, return all sources so user still gets links
    if not cited:
        return [
            SearchResult(
                title=r.title,
                url=r.url,
                snippet=r.snippet[:200] if r.snippet else "",
                source="synthesizer",
            )
            for r in results
        ]
    return cited


class SynthesizerClient:
    """AI-powered search synthesis using existing infrastructure.

    Pipeline: SearXNG -> (optional Crawl4AI) -> llama-server -> parsed answer.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._searxng = SearXNGClient(config=self._config)
        self._crawl4ai = Crawl4AIClient(config=self._config)

    def _build_llm_payload(self, query: str, sources_text: str) -> dict:
        """Build the OpenAI-compatible chat completion payload."""
        return {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Question: {query}\n\nSearch Results:\n{sources_text}",
                },
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
            "stream": False,
        }

    def _parse_llm_response(self, data: dict) -> str:
        """Extract the assistant message from the LLM response."""
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("llama-server returned no choices")
        return choices[0].get("message", {}).get("content", "")

    def _scrape_pages_sync(self, urls: list[str]) -> dict[str, str]:
        """Scrape multiple pages in parallel using ThreadPoolExecutor."""
        deep_content: dict[str, str] = {}

        def _scrape_one(url: str) -> tuple[str, str]:
            try:
                content, _ = self._crawl4ai.search(url)
                return url, content
            except Exception:
                return url, ""

        with ThreadPoolExecutor(max_workers=_DEEP_SCRAPE_COUNT) as executor:
            futures = [executor.submit(_scrape_one, u) for u in urls[:_DEEP_SCRAPE_COUNT]]
            for f in futures:
                url, content = f.result()
                if content:
                    deep_content[url] = content

        return deep_content

    async def _scrape_pages_async(self, urls: list[str]) -> dict[str, str]:
        """Scrape multiple pages in parallel using asyncio.gather."""
        deep_content: dict[str, str] = {}

        async def _scrape_one(url: str) -> tuple[str, str]:
            try:
                content, _ = await self._crawl4ai.asearch(url)
                return url, content
            except Exception:
                return url, ""

        tasks = [_scrape_one(u) for u in urls[:_DEEP_SCRAPE_COUNT]]
        results = await asyncio.gather(*tasks)
        for url, content in results:
            if content:
                deep_content[url] = content

        return deep_content

    def search(
        self,
        query: str,
        *,
        deep: bool = False,
        max_sources: int = 5,
    ) -> tuple[str, list[SearchResult]]:
        """Synchronous AI-powered search.

        Args:
            query: Search query.
            deep: If True, scrape top pages via Crawl4AI for richer context (~60s).
                  If False, use SearXNG snippets only (~30s).
            max_sources: Max number of sources to include in LLM context.

        Returns:
            Tuple of (synthesized answer, list of cited SearchResults).
        """
        # 1. Search SearXNG (retry transient empties, then degrade gracefully)
        search_results: list[SearchResult] = []
        for attempt in range(_SEARCH_RETRIES):
            search_results = self._searxng.search(query, max_results=max_sources)
            if search_results:
                break
            if attempt < _SEARCH_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF_S)
        if not search_results:
            # Engines in cooldown — no web results to synthesize from. Return an empty
            # answer (engine reports "empty", not "failed") instead of raising, so the
            # judge simply uses the other engines.
            return "", []

        # 2. Optionally scrape top pages for richer context
        deep_content: dict[str, str] | None = None
        if deep:
            urls = [r.url for r in search_results if r.url]
            deep_content = self._scrape_pages_sync(urls)

        # 3. Build prompt and call llama-server
        sources_text = _format_sources(search_results, deep_content=deep_content)
        payload = self._build_llm_payload(query, sources_text)

        llm_url = self._config.llama_url
        client = get_client(llm_url, self._config.synthesizer_timeout)
        resp = client.post(
            f"{llm_url}/v1/chat/completions",
            json=payload,
        )
        resp.raise_for_status()

        # 4. Parse and return
        answer = self._parse_llm_response(resp.json())
        cited = _extract_sources(answer, search_results)
        return answer, cited

    async def asearch(
        self,
        query: str,
        *,
        deep: bool = False,
        max_sources: int = 5,
    ) -> tuple[str, list[SearchResult]]:
        """Async AI-powered search.

        Args:
            query: Search query.
            deep: If True, scrape top pages via Crawl4AI for richer context (~60s).
                  If False, use SearXNG snippets only (~30s).
            max_sources: Max number of sources to include in LLM context.

        Returns:
            Tuple of (synthesized answer, list of cited SearchResults).
        """
        # 1. Search SearXNG (retry transient empties, then degrade gracefully)
        search_results: list[SearchResult] = []
        for attempt in range(_SEARCH_RETRIES):
            search_results = await self._searxng.asearch(query, max_results=max_sources)
            if search_results:
                break
            if attempt < _SEARCH_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF_S)
        if not search_results:
            # Engines in cooldown — no web results to synthesize from. Return an empty
            # answer (engine reports "empty", not "failed") instead of raising, so the
            # judge simply uses the other engines.
            return "", []

        # 2. Optionally scrape top pages for richer context
        deep_content: dict[str, str] | None = None
        if deep:
            urls = [r.url for r in search_results if r.url]
            deep_content = await self._scrape_pages_async(urls)

        # 3. Build prompt and call llama-server
        sources_text = _format_sources(search_results, deep_content=deep_content)
        payload = self._build_llm_payload(query, sources_text)

        llm_url = self._config.llama_url
        client = get_async_client(llm_url, self._config.synthesizer_timeout)
        resp = await client.post(
            f"{llm_url}/v1/chat/completions",
            json=payload,
        )
        resp.raise_for_status()

        # 4. Parse and return
        answer = self._parse_llm_response(resp.json())
        cited = _extract_sources(answer, search_results)
        return answer, cited
