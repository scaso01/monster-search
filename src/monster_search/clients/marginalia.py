"""Marginalia independent search engine client."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class MarginaliaClient:
    """Client for Marginalia independent open web search engine."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, data: dict, max_results: int) -> list[SearchResult]:
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    source="marginalia",
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via Marginalia API."""
        max_results = max_results or self._config.max_results
        client = get_client(self._config.marginalia_url, self._config.marginalia_timeout)
        resp = client.get(
            f"{self._config.marginalia_url}/search",
            params={"query": query, "count": max_results},
            headers={"API-Key": self._config.marginalia_api_key},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via Marginalia API."""
        max_results = max_results or self._config.max_results
        client = get_async_client(self._config.marginalia_url, self._config.marginalia_timeout)
        resp = await client.get(
            f"{self._config.marginalia_url}/search",
            params={"query": query, "count": max_results},
            headers={"API-Key": self._config.marginalia_api_key},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
