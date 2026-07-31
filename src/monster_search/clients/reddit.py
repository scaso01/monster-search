"""Reddit search client.

Uses the Atom/RSS feed endpoint (``/search.rss``) because Reddit now
returns HTTP 403 on the public JSON API for automated requests.  The RSS
feed is still publicly accessible and returns post titles, URLs,
subreddit labels, dates, and HTML content snippets.

The feed must be fetched from ``www.reddit.com``.  ``old.reddit.com`` used to
serve it and now answers ``/search.rss`` with HTTP 200 and an HTML page, and
the bare ``reddit.com`` apex answers 429, so both of those look like success
to a naive client and then fail at the parser.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://www.reddit.com"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


class RedditClient:
    """Search Reddit via RSS/Atom feed.

    Reddit blocks the public JSON API with 403 for automated clients.
    The ``/search.rss`` endpoint on ``old.reddit.com`` still works and
    returns Atom XML with post data.
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _parse_feed(xml_text: str, max_results: int) -> list[SearchResult]:
        # Reddit answers with HTTP 200 and an HTML page when it decides to
        # block the request, so nothing fails until here. Most such pages are
        # not well-formed and raise below, but a simple one parses cleanly and
        # would otherwise be reported as "no results found", which is worse
        # than an error because it looks like a real answer.
        preview = xml_text.lstrip()[:80].replace("\n", " ")
        _not_a_feed = RuntimeError(
            "reddit did not return an Atom feed, which usually means the "
            f"request was blocked. Response began: {preview!r}"
        )
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise _not_a_feed from exc
        if root.tag != "{http://www.w3.org/2005/Atom}feed":
            raise _not_a_feed
        entries = root.findall("atom:entry", _ATOM_NS)
        results: list[SearchResult] = []
        for entry in entries[:max_results]:
            title_el = entry.find("atom:title", _ATOM_NS)
            link_el = entry.find("atom:link", _ATOM_NS)
            updated_el = entry.find("atom:updated", _ATOM_NS)
            category_el = entry.find("atom:category", _ATOM_NS)
            content_el = entry.find("atom:content", _ATOM_NS)

            raw_title = title_el.text if title_el is not None and title_el.text else ""
            url = link_el.get("href", "") if link_el is not None else ""
            # Normalise to a bare reddit.com link. Entries come back as
            # www.reddit.com now, and older cached feeds still carry
            # old.reddit.com, so both are folded down.
            url = url.replace("https://old.reddit.com", "https://reddit.com")
            url = url.replace("https://www.reddit.com", "https://reddit.com")

            subreddit = ""
            if category_el is not None:
                subreddit = category_el.get("label", "") or ""

            display_title = f"{raw_title} [{subreddit}]" if subreddit else raw_title

            snippet = ""
            if content_el is not None and content_el.text:
                snippet = _strip_html(content_el.text)[:500]

            published = None
            if updated_el is not None and updated_el.text:
                # "2026-03-29T02:00:16+00:00" -> "2026-03-29 02:00 UTC"
                try:
                    ts = updated_el.text.replace("+00:00", "Z").replace("Z", "+00:00")
                    from datetime import datetime, timezone

                    dt = datetime.fromisoformat(ts)
                    published = dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                except (ValueError, TypeError):
                    published = updated_el.text[:10]

            results.append(
                SearchResult(
                    title=display_title,
                    url=url,
                    snippet=snippet,
                    source="reddit",
                    published=published,
                )
            )
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search via Reddit RSS feed."""
        max_results = max_results or self._config.max_results
        timeout = self._config.reddit_timeout
        with httpx.Client(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(
                f"{_BASE_URL}/search.rss",
                params={"q": query, "limit": max_results, "sort": "relevance", "type": "link"},
            )
            resp.raise_for_status()
            return self._parse_feed(resp.text, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search via Reddit RSS feed."""
        max_results = max_results or self._config.max_results
        timeout = self._config.reddit_timeout
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                f"{_BASE_URL}/search.rss",
                params={"q": query, "limit": max_results, "sort": "relevance", "type": "link"},
            )
            resp.raise_for_status()
            return self._parse_feed(resp.text, max_results)
