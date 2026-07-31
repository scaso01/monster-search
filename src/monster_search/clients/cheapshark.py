"""CheapShark PC game price comparison — 30+ stores, no API key needed."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://www.cheapshark.com"


class CheapSharkClient:
    """Search PC game deals across 30+ stores via CheapShark API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _parse_results(data: list[dict], max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for deal in data[:max_results]:
            title = deal.get("title", "")
            deal_id = deal.get("dealID", "")
            sale_price = deal.get("salePrice", "0.00")
            normal_price = deal.get("normalPrice", "0.00")
            savings_raw = deal.get("savings", "0")
            store_id = deal.get("storeID", "")

            try:
                savings_pct = float(savings_raw)
            except (ValueError, TypeError):
                savings_pct = 0.0

            url = f"{_BASE_URL}/redirect?dealID={deal_id}"
            snippet = (
                f"Sale: ${sale_price} (was ${normal_price}, "
                f"save {savings_pct:.0f}%) | Store ID: {store_id}"
            )

            results.append(SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                source="cheapshark",
                price=f"${sale_price}",
                in_stock=True,
            ))
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search for game deals."""
        max_results = max_results or self._config.max_results
        timeout = self._config.cheapshark_timeout
        client = get_client(_BASE_URL, timeout)
        resp = client.get(
            f"{_BASE_URL}/api/1.0/deals",
            params={"title": query, "onSale": 1, "pageSize": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search for game deals."""
        max_results = max_results or self._config.max_results
        timeout = self._config.cheapshark_timeout
        client = get_async_client(_BASE_URL, timeout)
        resp = await client.get(
            f"{_BASE_URL}/api/1.0/deals",
            params={"title": query, "onSale": 1, "pageSize": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
