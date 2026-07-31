"""Google News RSS client."""

from __future__ import annotations

import re

import feedparser
import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

GNEWS_RSS_BASE = "https://news.google.com/rss/search"


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


class GNewsClient:
    """Client for Google News via RSS feed."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _resolve_url(self, google_url: str, client: httpx.Client) -> str:
        """Resolve Google redirect URL to actual article URL via HEAD."""
        try:
            resp = client.head(google_url, follow_redirects=True)
            return str(resp.url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return google_url

    async def _aresolve_url(self, google_url: str, client: httpx.AsyncClient) -> str:
        """Async resolve Google redirect URL to actual article URL via HEAD."""
        try:
            resp = await client.head(google_url, follow_redirects=True)
            return str(resp.url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return google_url

    def _parse_results(
        self, feed: feedparser.FeedParserDict, max_results: int, client: httpx.Client
    ) -> list[SearchResult]:
        results = []
        for entry in feed.entries[:max_results]:
            url = self._resolve_url(entry.get("link", ""), client)
            description = _strip_html(entry.get("description", ""))
            snippet = description[:500] if description else ""
            results.append(
                SearchResult(
                    title=entry.get("title", ""),
                    url=url,
                    snippet=snippet,
                    source="gnews",
                    published=entry.get("published", None),
                    category="news",
                )
            )
        return results

    async def _aparse_results(
        self, feed: feedparser.FeedParserDict, max_results: int, client: httpx.AsyncClient
    ) -> list[SearchResult]:
        results = []
        for entry in feed.entries[:max_results]:
            url = await self._aresolve_url(entry.get("link", ""), client)
            description = _strip_html(entry.get("description", ""))
            snippet = description[:500] if description else ""
            results.append(
                SearchResult(
                    title=entry.get("title", ""),
                    url=url,
                    snippet=snippet,
                    source="gnews",
                    published=entry.get("published", None),
                    category="news",
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search via Google News RSS."""
        max_results = max_results or self._config.max_results
        client = get_client(GNEWS_RSS_BASE, self._config.gnews_timeout)
        resp = client.get(
            GNEWS_RSS_BASE,
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        return self._parse_results(feed, max_results, client)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search via Google News RSS."""
        max_results = max_results or self._config.max_results
        client = get_async_client(GNEWS_RSS_BASE, self._config.gnews_timeout)
        resp = await client.get(
            GNEWS_RSS_BASE,
            params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        return await self._aparse_results(feed, max_results, client)
