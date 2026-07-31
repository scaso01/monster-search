"""Hacker News search via Algolia API."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://hn.algolia.com"


class HackerNewsClient:
    """Search Hacker News stories and comments via Algolia HN Search API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _parse_results(data: dict, max_results: int) -> list[SearchResult]:
        hits = data.get("hits", [])
        results: list[SearchResult] = []
        for hit in hits[:max_results]:
            title = hit.get("title") or hit.get("story_title", "")
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            comment_text = hit.get("comment_text", "")
            if comment_text:
                snippet = comment_text[:500]
            else:
                points = hit.get("points", 0)
                num_comments = hit.get("num_comments", 0)
                snippet = f"{points} points, {num_comments} comments"
            published = hit.get("created_at", "")
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                source="hackernews",
                published=published or None,
            ))
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search via Algolia HN Search API."""
        max_results = max_results or self._config.max_results
        timeout = self._config.hackernews_timeout
        client = get_client(_BASE_URL, timeout)
        resp = client.get(
            f"{_BASE_URL}/api/v1/search",
            params={"query": query, "hitsPerPage": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search via Algolia HN Search API."""
        max_results = max_results or self._config.max_results
        timeout = self._config.hackernews_timeout
        client = get_async_client(_BASE_URL, timeout)
        resp = await client.get(
            f"{_BASE_URL}/api/v1/search",
            params={"query": query, "hitsPerPage": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
