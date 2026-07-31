"""Semantic Scholar academic paper search client."""

from __future__ import annotations

import asyncio
import time


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = "title,abstract,url,year,citationCount,authors,openAccessPdf,publicationDate"
_RETRY_DELAYS = (3, 6, 12)
_USER_AGENT = "monster-search/0.10.0 (research tool)"

# Message shown when MONSTER_SEMANTIC_SCHOLAR_API_KEY is not configured.
# A free key avoids rate limits and enables higher quotas.
_NO_KEY_MSG = (
    "Semantic Scholar requires a free API key.  "
    "Get one at https://www.semanticscholar.org/product/api#api-key "
    "then set MONSTER_SEMANTIC_SCHOLAR_API_KEY in your environment "
    "or in a .env file"
)

# Message shown when a 429 is received even with a key set.
_RATE_LIMITED_WITH_KEY_MSG = (
    "Semantic Scholar rate-limited even with API key — try again later."
)


class SemanticScholarClient:
    """Client for Semantic Scholar academic paper search API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _require_api_key(self) -> None:
        """Raise immediately with a helpful message when no API key is configured.

        Without a key, every request to the Semantic Scholar API from a
        shared/VPN exit IP results in an immediate 429.  Failing fast here
        saves the round-trip and gives the user an actionable message.
        """
        if not self._config.semantic_scholar_api_key:
            raise RuntimeError(_NO_KEY_MSG)

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": _USER_AGENT}
        if self._config.semantic_scholar_api_key:
            headers["x-api-key"] = self._config.semantic_scholar_api_key
        return headers

    def _parse_results(self, data: dict, max_results: int) -> list[SearchResult]:
        results = []
        for item in data.get("data", [])[:max_results]:
            abstract = item.get("abstract") or ""
            if len(abstract) > 500:
                abstract = abstract[:500]
            citation_count = item.get("citationCount")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=abstract,
                    source="semantic_scholar",
                    published=item.get("publicationDate"),
                    score=float(citation_count) if citation_count is not None else None,
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via Semantic Scholar API."""
        self._require_api_key()
        max_results = max_results or self._config.max_results
        client = get_client(_BASE_URL, self._config.semantic_scholar_timeout)
        params = {
            "query": query,
            "fields": _FIELDS,
            "limit": max_results,
        }
        for attempt in range(len(_RETRY_DELAYS) + 1):
            resp = client.get(
                _BASE_URL,
                params=params,
                headers=self._headers(),
            )
            if resp.status_code != 429:
                resp.raise_for_status()
                return self._parse_results(resp.json(), max_results)
            if attempt < len(_RETRY_DELAYS):
                time.sleep(_RETRY_DELAYS[attempt])
        raise RuntimeError(_RATE_LIMITED_WITH_KEY_MSG)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via Semantic Scholar API."""
        self._require_api_key()
        max_results = max_results or self._config.max_results
        client = get_async_client(_BASE_URL, self._config.semantic_scholar_timeout)
        params = {
            "query": query,
            "fields": _FIELDS,
            "limit": max_results,
        }
        for attempt in range(len(_RETRY_DELAYS) + 1):
            resp = await client.get(
                _BASE_URL,
                params=params,
                headers=self._headers(),
            )
            if resp.status_code != 429:
                resp.raise_for_status()
                return self._parse_results(resp.json(), max_results)
            if attempt < len(_RETRY_DELAYS):
                await asyncio.sleep(_RETRY_DELAYS[attempt])
        raise RuntimeError(_RATE_LIMITED_WITH_KEY_MSG)
