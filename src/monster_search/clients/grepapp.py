"""grep.app public code search client."""

from __future__ import annotations

import os
import time

import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://grep.app"
_DEFAULT_USER_AGENT = "monster-search/0.10.0 (self-hosted research tool)"
# grep.app aggressively rate-limits cloud/VPN exit IPs.  The retry-after
# header tells us how long to wait; if absent, fall back to this default.
_FALLBACK_RETRY_AFTER_SECONDS = 60


def _rate_limit_error(retry_after: int | None) -> httpx.HTTPStatusError:
    """Build an informative 429 error for grep.app."""
    if retry_after:
        wait_msg = f"retry after {retry_after}s"
    else:
        wait_msg = f"retry after ~{_FALLBACK_RETRY_AFTER_SECONDS}s"
    msg = (
        f"grep.app returned HTTP 429 — VPN/shared exit IP is rate-limited "
        f"({wait_msg}).  "
        "Try again later, or set MONSTER_GREPAPP_USER_AGENT in your .env "
        "to a descriptive string that identifies your instance."
    )
    # Raise a plain RuntimeError so callers get the full human-readable message
    # without needing to inspect a response object.
    raise RuntimeError(msg)


class GrepAppClient:
    """Search public GitHub repos via grep.app API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        # Allow overriding the User-Agent via env var so operators can
        # identify their instance to upstream services.
        self._user_agent: str = (
            os.environ.get("MONSTER_GREPAPP_USER_AGENT", "").strip()
            or _DEFAULT_USER_AGENT
        )

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent}

    def _parse_results(self, data: dict, max_results: int) -> list[SearchResult]:
        """Parse grep.app API response into SearchResults."""
        hits = data.get("hits", {}).get("hits", [])
        results: list[SearchResult] = []
        for hit in hits[:max_results]:
            repo = hit.get("repo", {}).get("raw", "")
            path = hit.get("path", {}).get("raw", "")
            snippet = hit.get("content", {}).get("snippet", "")
            language = hit.get("language", {}).get("raw", "")
            results.append(SearchResult(
                title=path,
                url=f"https://github.com/{repo}/blob/HEAD/{path}",
                snippet=snippet,
                source="grepapp",
                category=language,
            ))
        return results

    def _retry_after(self, resp: httpx.Response) -> int | None:
        """Parse the Retry-After header (seconds) if present."""
        raw = resp.headers.get("retry-after", "").strip()
        if raw.isdigit():
            return int(raw)
        return None

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via grep.app API."""
        max_results = max_results or self._config.max_results
        timeout = self._config.grepapp_timeout
        client = get_client(_BASE_URL, timeout)
        resp = client.get(
            f"{_BASE_URL}/api/search",
            params={"q": query},
            headers=self._headers(),
        )
        if resp.status_code == 429:
            retry_after = self._retry_after(resp)
            if retry_after is not None:
                time.sleep(retry_after)
                resp = client.get(
                    f"{_BASE_URL}/api/search",
                    params={"q": query},
                    headers=self._headers(),
                )
                if resp.status_code != 429:
                    resp.raise_for_status()
                    return self._parse_results(resp.json(), max_results)
            _rate_limit_error(retry_after)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via grep.app API."""
        import asyncio

        max_results = max_results or self._config.max_results
        timeout = self._config.grepapp_timeout
        client = get_async_client(_BASE_URL, timeout)
        resp = await client.get(
            f"{_BASE_URL}/api/search",
            params={"q": query},
            headers=self._headers(),
        )
        if resp.status_code == 429:
            retry_after = self._retry_after(resp)
            if retry_after is not None:
                await asyncio.sleep(retry_after)
                resp = await client.get(
                    f"{_BASE_URL}/api/search",
                    params={"q": query},
                    headers=self._headers(),
                )
                if resp.status_code != 429:
                    resp.raise_for_status()
                    return self._parse_results(resp.json(), max_results)
            _rate_limit_error(retry_after)
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
