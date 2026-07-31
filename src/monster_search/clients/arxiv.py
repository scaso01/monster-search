"""arXiv preprint search client."""

from __future__ import annotations

import asyncio
import time

import feedparser
import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://export.arxiv.org/api/query"
_RETRY_STATUSES = {429, 503}
_RETRY_DELAYS = (5, 10, 20)


class ArxivClient:
    """Client for arXiv paper search API (Atom feed)."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, text: str, max_results: int) -> list[SearchResult]:
        feed = feedparser.parse(text)
        results = []
        for entry in feed.entries[:max_results]:
            summary = entry.get("summary", "")
            if len(summary) > 500:
                summary = summary[:500]
            primary_category = entry.get("arxiv_primary_category", {}).get("term", "")
            results.append(
                SearchResult(
                    title=entry.get("title", ""),
                    url=entry.get("id", ""),
                    snippet=summary,
                    source="arxiv",
                    published=entry.get("published"),
                    category=primary_category,
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via arXiv API."""
        max_results = max_results or self._config.max_results
        client = get_client(_BASE_URL, self._config.arxiv_timeout)
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                resp = client.get(_BASE_URL, params=params)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    time.sleep(_RETRY_DELAYS[attempt])
                continue
            if resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                return self._parse_results(resp.text, max_results)
            last_exc = httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp,
            )
            if attempt < len(_RETRY_DELAYS):
                retry_after = resp.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else _RETRY_DELAYS[attempt]
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via arXiv API."""
        max_results = max_results or self._config.max_results
        client = get_async_client(_BASE_URL, self._config.arxiv_timeout)
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        last_exc: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                resp = await client.get(_BASE_URL, params=params)
            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            if resp.status_code not in _RETRY_STATUSES:
                resp.raise_for_status()
                return self._parse_results(resp.text, max_results)
            last_exc = httpx.HTTPStatusError(
                f"{resp.status_code} {resp.reason_phrase}", request=resp.request, response=resp,
            )
            if attempt < len(_RETRY_DELAYS):
                retry_after = resp.headers.get("Retry-After")
                delay = int(retry_after) if retry_after and retry_after.isdigit() else _RETRY_DELAYS[attempt]
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]
