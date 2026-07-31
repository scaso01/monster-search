"""News engine — thin wrapper around SearXNG with category=news."""

from __future__ import annotations

from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.models import SearchResult


class NewsSearchClient:
    """Runs SearXNG with category=news and sorts results by date."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._searxng = SearXNGClient(config=self._config)

    @staticmethod
    def _sort_by_date(results: list[SearchResult]) -> list[SearchResult]:
        """Sort results by published date (newest first), undated last."""
        results.sort(key=lambda r: r.published or "", reverse=True)
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        time_range: str | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Synchronous search via SearXNG news category, sort by date."""
        max_results = max_results or self._config.max_results

        results = self._searxng.search(
            query, category="news", time_range=time_range, max_results=max_results
        )

        return "", self._sort_by_date(results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
        time_range: str | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Async search via SearXNG news category, sort by date."""
        max_results = max_results or self._config.max_results

        results = await self._searxng.asearch(
            query, category="news", time_range=time_range, max_results=max_results
        )

        return "", self._sort_by_date(results)
