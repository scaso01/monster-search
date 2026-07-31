"""OpenAlex academic works search client."""

from __future__ import annotations


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://api.openalex.org/works"
_SELECT = "id,doi,title,display_name,publication_year,cited_by_count,open_access,authorships,abstract_inverted_index"


def _reconstruct_abstract(inverted_index: dict | None) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format.

    The inverted index is a dict mapping words to lists of positions,
    e.g. {"Neural": [0], "networks": [1], "are": [2, 5]}.
    """
    if not inverted_index:
        return ""
    pairs: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            pairs.append((pos, word))
    pairs.sort(key=lambda p: p[0])
    text = " ".join(word for _, word in pairs)
    if len(text) > 500:
        text = text[:500]
    return text


class OpenAlexClient:
    """Client for OpenAlex academic works search API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, data: dict, max_results: int) -> list[SearchResult]:
        results = []
        for item in data.get("results", [])[:max_results]:
            abstract = _reconstruct_abstract(item.get("abstract_inverted_index"))
            doi = item.get("doi")
            url = doi if doi else item.get("id", "")
            cited_by_count = item.get("cited_by_count")
            pub_year = item.get("publication_year")
            results.append(
                SearchResult(
                    title=item.get("display_name", ""),
                    url=url,
                    snippet=abstract,
                    source="openalex",
                    published=str(pub_year) if pub_year is not None else None,
                    score=float(cited_by_count) if cited_by_count is not None else None,
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via OpenAlex API."""
        max_results = max_results or self._config.max_results
        params: dict = {
            "search": query,
            "per_page": max_results,
            "select": _SELECT,
        }
        if self._config.openalex_mailto:
            params["mailto"] = self._config.openalex_mailto
        client = get_client(_BASE_URL, self._config.openalex_timeout)
        resp = client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via OpenAlex API."""
        max_results = max_results or self._config.max_results
        params: dict = {
            "search": query,
            "per_page": max_results,
            "select": _SELECT,
        }
        if self._config.openalex_mailto:
            params["mailto"] = self._config.openalex_mailto
        client = get_async_client(_BASE_URL, self._config.openalex_timeout)
        resp = await client.get(_BASE_URL, params=params)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
