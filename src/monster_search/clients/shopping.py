"""Shopping engine — thin wrapper around SearXNG with category=shopping."""

from __future__ import annotations

from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.models import SearchResult


class ShoppingSearchClient:
    """Runs SearXNG with category=shopping for product/price results."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._searxng = SearXNGClient(config=self._config)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via SearXNG shopping category."""
        max_results = max_results or self._config.max_results
        return self._searxng.search(
            query, category="shopping", max_results=max_results
        )

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via SearXNG shopping category."""
        max_results = max_results or self._config.max_results
        return await self._searxng.asearch(
            query, category="shopping", max_results=max_results
        )
