"""SearXNG JSON API client."""

from __future__ import annotations


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class SearXNGClient:
    """Client for SearXNG meta-search engine."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, data: dict, max_results: int) -> list[SearchResult]:
        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    source="searxng",
                    engine=item.get("engine"),
                    score=item.get("score"),
                    published=item.get("publishedDate"),
                    category=item.get("category"),
                    price=item.get("price") or None,
                    in_stock=item.get("in_stock"),
                )
            )
        return results

    def _build_params(
        self,
        query: str,
        category: str | None,
        engines: str | None,
        time_range: str | None,
        page: int,
    ) -> dict:
        params: dict[str, str | int] = {"q": query, "format": "json", "pageno": page}
        if category:
            params["categories"] = category
        if engines:
            params["engines"] = engines
        if time_range:
            params["time_range"] = time_range
        return params

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        engines: str | None = None,
        time_range: str | None = None,
        max_results: int | None = None,
        page: int = 1,
    ) -> list[SearchResult]:
        """Synchronous search via SearXNG JSON API."""
        max_results = max_results or self._config.max_results
        params = self._build_params(query, category, engines, time_range, page)
        client = get_client(self._config.searxng_url, self._config.timeout)
        resp = client.get(f"{self._config.searxng_url}/search", params=params)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        category: str | None = None,
        engines: str | None = None,
        time_range: str | None = None,
        max_results: int | None = None,
        page: int = 1,
    ) -> list[SearchResult]:
        """Async search via SearXNG JSON API."""
        max_results = max_results or self._config.max_results
        params = self._build_params(query, category, engines, time_range, page)
        client = get_async_client(self._config.searxng_url, self._config.timeout)
        resp = await client.get(f"{self._config.searxng_url}/search", params=params)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
