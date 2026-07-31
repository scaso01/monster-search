"""Mwmbl independent open-source search engine client."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


def _join_tokens(tokens: object) -> str:
    """Mwmbl returns title/extract as a list of {value, is_bold} tokens — join the values."""
    if not isinstance(tokens, list):
        return str(tokens or "")
    return "".join(t.get("value", "") for t in tokens if isinstance(t, dict)).strip()


class MwmblClient:
    """Client for Mwmbl — a non-profit, open-source, independent web index.

    Free, keyless public API. Unlike a metasearch engine, Mwmbl crawls its own
    index (independent of Google/Bing), so it surfaces indie/long-tail pages the
    other web engines miss — paired with Marginalia in the web-general tier. The
    API returns a JSON LIST of results, each with a tokenized ``title`` and
    ``extract`` (lists of ``{value, is_bold}``), which we flatten to plain text.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(self, data: object, max_results: int) -> list[SearchResult]:
        items = data if isinstance(data, list) else []
        results = []
        for item in items[:max_results]:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResult(
                    title=_join_tokens(item.get("title")),
                    url=item.get("url", ""),
                    snippet=_join_tokens(item.get("extract")),
                    source="mwmbl",
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via the Mwmbl public API."""
        max_results = max_results or self._config.max_results
        client = get_client(self._config.mwmbl_url, self._config.mwmbl_timeout)
        resp = client.get(
            f"{self._config.mwmbl_url}/api/v1/search/",
            params={"s": query},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via the Mwmbl public API."""
        max_results = max_results or self._config.max_results
        client = get_async_client(self._config.mwmbl_url, self._config.mwmbl_timeout)
        resp = await client.get(
            f"{self._config.mwmbl_url}/api/v1/search/",
            params={"s": query},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
