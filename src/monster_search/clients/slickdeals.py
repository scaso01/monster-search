"""Slickdeals RSS search client for curated deals."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus

import feedparser

from monster_search.config import Config
from monster_search.models import SearchResult

_SEARCH_URL = "https://slickdeals.net/newsearch.php"

_PRICE_RE = re.compile(r"\$[\d,]+\.?\d*")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return _HTML_TAG_RE.sub("", text)


def _extract_price(text: str) -> str | None:
    """Extract the first dollar price from text, or None."""
    match = _PRICE_RE.search(text)
    return match.group(0) if match else None


class SlickdealsClient:
    """Search Slickdeals curated deals via RSS feed."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _build_url(self, query: str) -> str:
        """Build the Slickdeals RSS search URL."""
        return (
            f"{_SEARCH_URL}?q={quote_plus(query)}"
            f"&searcharea=deals&searchin=first&rss=1"
        )

    @staticmethod
    def _parse_results(
        feed: feedparser.FeedParserDict, max_results: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        for entry in feed.entries[:max_results]:
            title = entry.get("title", "")
            link = entry.get("link", "")
            raw_description = entry.get("description", "")
            snippet = _strip_html(raw_description).strip()

            # Extract price from title first, then description
            price = _extract_price(title) or _extract_price(raw_description)

            published = entry.get("published", None)

            results.append(
                SearchResult(
                    title=title,
                    url=link,
                    snippet=snippet[:500] if snippet else "",
                    source="slickdeals",
                    published=published,
                    price=price,
                    in_stock=None,
                )
            )
        return results

    def _fetch_feed(self, query: str) -> feedparser.FeedParserDict:
        """Synchronously fetch and parse the RSS feed."""
        url = self._build_url(query)
        return feedparser.parse(url)

    def search(
        self, query: str, *, max_results: int | None = None
    ) -> list[SearchResult]:
        """Synchronous search via Slickdeals RSS."""
        max_results = max_results or self._config.max_results
        feed = self._fetch_feed(query)
        return self._parse_results(feed, max_results)

    async def asearch(
        self, query: str, *, max_results: int | None = None
    ) -> list[SearchResult]:
        """Async search via Slickdeals RSS (runs feedparser in executor)."""
        max_results = max_results or self._config.max_results
        loop = asyncio.get_event_loop()
        feed = await loop.run_in_executor(None, self._fetch_feed, query)
        return self._parse_results(feed, max_results)
