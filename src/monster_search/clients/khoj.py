"""Khoj AI chat/search client.

Uses the ``/online`` command prefix to skip Khoj's tool-selection LLM call
and go directly to web search, saving 20-60s per request.

Response structure (non-streaming):
    {
      "response": "...",
      "references": {
        "onlineContext": {
          "<subquery>": {"organic": [{"title":..,"link":..,"description":..}, ...]},
          ...
        }
      },
      ...
    }
"""

from __future__ import annotations

import re

import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# Strip residual thinking tags leaked by reasoning-capable models
_THINK_RE = re.compile(r"^</think>\s*", re.DOTALL)


def _extract_online_sources(online_context: dict) -> list[SearchResult]:
    """Extract sources from Khoj onlineContext.

    The onlineContext dict maps *subquery strings* to result dicts, each
    containing an ``organic`` list of ``{title, link, description}`` items.
    It can also map *URLs* directly to snippet strings/dicts (older format).
    """
    results: list[SearchResult] = []
    seen: set[str] = set()
    for key, value in online_context.items():
        # New format: key is a subquery, value has {"organic": [...]}
        if isinstance(value, dict) and "organic" in value:
            for item in value["organic"]:
                link = item.get("link", "")
                if not link or link in seen:
                    continue
                seen.add(link)
                results.append(
                    SearchResult(
                        title=item.get("title", link.split("/")[-1] or link),
                        url=link,
                        snippet=(item.get("description", "") or "")[:500],
                        source="khoj",
                    )
                )
        else:
            # Legacy format: key is a URL, value is snippet string/dict
            url = key
            if url in seen:
                continue
            seen.add(url)
            snippet = ""
            if isinstance(value, str):
                snippet = value[:500]
            elif isinstance(value, dict):
                snippet = (value.get("snippet", "") or value.get("content", "") or "")[:500]
            results.append(
                SearchResult(
                    title=url.split("/")[-1] or url,
                    url=url,
                    snippet=snippet,
                    source="khoj",
                )
            )
    return results


class KhojClient:
    """Client for Khoj AI search (anonymous mode, no auth).

    Prefixes queries with ``/online`` so Khoj skips tool-selection and
    goes straight to web search (saves one full LLM round-trip).
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _prepare_query(query: str) -> str:
        """Prefix query with /online unless it already has a command."""
        stripped = query.lstrip()
        if stripped.startswith("/"):
            return query  # user already specified a command
        return f"/online {query}"

    def _parse_response(self, data: dict) -> tuple[str, list[SearchResult]]:
        """Parse Khoj API response."""
        message = data.get("response", "")
        # Strip residual </think> tag from reasoning models
        message = _THINK_RE.sub("", message)
        results: list[SearchResult] = []

        # onlineContext lives under "references" (non-streaming response)
        refs = data.get("references", {})
        online_context: dict = {}
        if isinstance(refs, dict):
            online_context = refs.get("onlineContext", {}) or {}
        # Fallback: also check top-level (streaming / older versions)
        if not online_context:
            online_context = data.get("onlineContext", {}) or {}

        if online_context:
            results = _extract_online_sources(online_context)

        # Fallback: extract URLs from response text if no onlineContext
        if not results and message:
            seen: set[str] = set()
            for url in _URL_RE.findall(message):
                if url in seen:
                    continue
                seen.add(url)
                results.append(
                    SearchResult(
                        title=url.split("/")[-1] or url,
                        url=url,
                        snippet="",
                        source="khoj",
                    )
                )

        return message, results

    def search(self, query: str) -> tuple[str, list[SearchResult]]:
        """Synchronous search via Khoj."""
        client = get_client(self._config.khoj_url, self._config.khoj_timeout)
        try:
            resp = client.post(
                f"{self._config.khoj_url}/api/chat",
                json={
                    "q": self._prepare_query(query),
                    "create_new": True,
                    "stream": False,
                },
            )
        except httpx.ReadTimeout:
            raise RuntimeError(
                f"khoj read timeout after {self._config.khoj_timeout}s "
                f"(llama-server likely saturated)"
            )
        resp.raise_for_status()
        return self._parse_response(resp.json())

    async def asearch(self, query: str) -> tuple[str, list[SearchResult]]:
        """Async search via Khoj."""
        client = get_async_client(self._config.khoj_url, self._config.khoj_timeout)
        try:
            resp = await client.post(
                f"{self._config.khoj_url}/api/chat",
                json={
                    "q": self._prepare_query(query),
                    "create_new": True,
                    "stream": False,
                },
            )
        except httpx.ReadTimeout:
            raise RuntimeError(
                f"khoj read timeout after {self._config.khoj_timeout}s "
                f"(llama-server likely saturated)"
            )
        resp.raise_for_status()
        return self._parse_response(resp.json())
