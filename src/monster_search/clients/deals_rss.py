"""Multi-feed RSS deal aggregator client.

Aggregates deals from 8 RSS feeds: r/buildapcsales, DealNews, GottaDeal,
DealNews Computers, DealNews Electronics, Ben's Bargains, r/deals, and
r/GameDeals.  Uses feedparser for XML parsing with a realistic User-Agent
to satisfy Reddit RSS requirements.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

import feedparser

from monster_search.config import Config
from monster_search.models import SearchResult

_FEEDS: list[tuple[str, str]] = [
    ("https://www.reddit.com/r/buildapcsales/.rss", "r/buildapcsales"),
    ("https://www.dealnews.com/?rss=1", "dealnews"),
    ("https://feeds.feedburner.com/GottaDealRSS", "gottadeal"),
    ("https://www.dealnews.com/c39/Computers/?rss=1", "dealnews-computers"),
    ("https://www.dealnews.com/c142/Electronics/?rss=1", "dealnews-electronics"),
    ("https://bensbargains.com/rss/", "bensbargains"),
    ("https://www.reddit.com/r/deals/.rss", "r/deals"),
    ("https://www.reddit.com/r/GameDeals/.rss", "r/GameDeals"),
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
)

_PRICE_RE = re.compile(r"\$[\d,]+\.?\d*")


class _HTMLStripper(HTMLParser):
    """Simple HTML tag stripper."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    stripper = _HTMLStripper()
    stripper.feed(html)
    text = stripper.get_text()
    return re.sub(r"\s+", " ", text).strip()


def _matches_query(title: str, query: str) -> bool:
    """Check if any query word (3+ chars) appears in the title.

    If ALL words are shorter than 3 chars, use them as-is to avoid
    ``any()`` returning ``False`` on an empty iterable.
    """
    title_lower = title.lower()
    words = query.lower().split()
    long_words = [w for w in words if len(w) >= 3]
    check = long_words if long_words else words
    return any(w in title_lower for w in check)


def _parse_feed(
    feed: feedparser.FeedParserDict,
    source_name: str,
    query: str,
) -> list[SearchResult]:
    """Parse a single feed into filtered SearchResult entries."""
    results: list[SearchResult] = []
    for entry in feed.entries:
        title = entry.get("title", "")
        if not _matches_query(title, query):
            continue
        link = entry.get("link", "")
        raw_desc = entry.get("description", "") or entry.get("summary", "")
        snippet = _strip_html(raw_desc)[:300] if raw_desc else ""
        published = entry.get("published", None)
        price_match = _PRICE_RE.search(title)
        price = price_match.group(0) if price_match else None
        results.append(
            SearchResult(
                title=title,
                url=link,
                snippet=snippet,
                source=source_name,
                published=published,
                price=price,
                in_stock=None,
            )
        )
    return results


def _fetch_and_parse(url: str, source_name: str, query: str) -> list[SearchResult]:
    """Fetch one RSS feed synchronously and parse results."""
    feed = feedparser.parse(url, agent=_USER_AGENT)
    return _parse_feed(feed, source_name, query)


class DealsRSSClient:
    """Aggregate deals from multiple RSS feeds."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search across all deal RSS feeds."""
        max_results = max_results or self._config.max_results
        all_results: list[SearchResult] = []
        for url, source_name in _FEEDS:
            all_results.extend(_fetch_and_parse(url, source_name, query))
        # Sort by published date descending (newest first); entries without
        # a date go to the end.
        all_results.sort(key=lambda r: r.published or "", reverse=True)
        return all_results[:max_results]

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search across all deal RSS feeds concurrently."""
        max_results = max_results or self._config.max_results
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(None, _fetch_and_parse, url, source_name, query)
            for url, source_name in _FEEDS
        ]
        feed_results = await asyncio.gather(*tasks)
        all_results: list[SearchResult] = []
        for results in feed_results:
            all_results.extend(results)
        all_results.sort(key=lambda r: r.published or "", reverse=True)
        return all_results[:max_results]
