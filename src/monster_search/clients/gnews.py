"""Google News RSS client."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from urllib.parse import quote, urlsplit

import feedparser
import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

GNEWS_RSS_BASE = "https://news.google.com/rss/search"

# Google News RSS links are encrypted redirects that a HEAD no longer follows, so the
# article URL is decoded through Google's own batchexecute call (ported from yt-intel's
# gnews.py, verified live 2026-09-23: 6/6 links in 3.3s). HEAD stays as the fallback.
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP = re.compile(r'data-n-a-ts="([^"]+)"')
# The inner array must be exactly 17 elements; an 18th makes Google answer `[3]`.
_REQUEST = (
    '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
    'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{article_id}",'
    '{timestamp},"{signature}"]'
)


def _is_google_news(url: str) -> bool:
    parts = urlsplit(url or "")
    segs = parts.path.split("/")
    return parts.hostname == "news.google.com" and len(segs) > 1 and segs[-2] in ("articles", "read")


def _article_page(url: str) -> str:
    return f"https://news.google.com/articles/{urlsplit(url).path.split('/')[-1]}"


def _batch_body(url: str, page_html: str) -> str | None:
    signature = _SIGNATURE.search(page_html)
    timestamp = _TIMESTAMP.search(page_html)
    if not (signature and timestamp):
        return None
    payload = ["Fbv4je", _REQUEST.format(article_id=urlsplit(url).path.split("/")[-1],
                                         timestamp=timestamp.group(1),
                                         signature=signature.group(1))]
    return f"f.req={quote(json.dumps([[payload]]))}"


def _decoded_url(response_text: str) -> str:
    # XSSI-guarded envelope: a `)]}'` line, a blank line, the payload, two bookkeeping frames.
    body = json.loads(response_text.split("\n\n")[1])[:-2]
    found = json.loads(body[0][2])[1]
    return found if isinstance(found, str) and found.startswith("http") else ""


_BATCH_HEADERS = {"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}


# Layer 3, from yt-intel: open the link in a real browser and read where it lands.
# Slow (~4s a link) but Google cannot break it without breaking Google News itself,
# and it fails for reasons unrelated to the batchexecute decode above.
BROWSER_LIMIT = 10


def _navigate(urls: list[str]) -> dict[str, str]:
    """{google news url: article url} for the links a headless browser resolved."""
    try:
        from patchright.sync_api import sync_playwright
    except ImportError:
        from playwright.sync_api import sync_playwright

    resolved: dict[str, str] = {}
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=_UA)
            for url in urls:
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                    page.wait_for_timeout(3_500)
                except Exception:  # one bad link must not lose the rest; it stays unresolved
                    continue
                if not _is_google_news(page.url):
                    resolved[url] = page.url
        finally:
            browser.close()
    return resolved


def _browser_pass(results: list[SearchResult]) -> list[SearchResult]:
    stubborn = [r.url for r in results if _is_google_news(r.url)][:BROWSER_LIMIT]
    if not stubborn:
        return results
    try:
        resolved = _navigate(stubborn)
    except Exception:  # no browser installed or it failed to launch; links stay unresolved
        return results
    return [replace(r, url=resolved[r.url]) if r.url in resolved else r for r in results]


def _strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    return re.sub(r"<[^>]+>", "", text)


class GNewsClient:
    """Client for Google News via RSS feed."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _decode(self, google_url: str, client: httpx.Client) -> str:
        page = client.get(_article_page(google_url), headers={"User-Agent": _UA}, follow_redirects=True)
        body = _batch_body(google_url, page.text) if page.status_code == 200 else None
        if not body:
            return ""
        resp = client.post(_BATCH_URL, content=body, headers=_BATCH_HEADERS)
        return _decoded_url(resp.text) if resp.status_code == 200 else ""

    async def _adecode(self, google_url: str, client: httpx.AsyncClient) -> str:
        page = await client.get(_article_page(google_url), headers={"User-Agent": _UA}, follow_redirects=True)
        body = _batch_body(google_url, page.text) if page.status_code == 200 else None
        if not body:
            return ""
        resp = await client.post(_BATCH_URL, content=body, headers=_BATCH_HEADERS)
        return _decoded_url(resp.text) if resp.status_code == 200 else ""

    def _resolve_url(self, google_url: str, client: httpx.Client) -> str:
        """Resolve a Google News redirect: batchexecute decode first, HEAD as fallback."""
        if _is_google_news(google_url):
            try:
                found = self._decode(google_url, client)
            except Exception:  # undocumented endpoint; any break drops to the HEAD fallback
                found = ""
            if found:
                return found
        try:
            resp = client.head(google_url, follow_redirects=True)
            return str(resp.url)
        except (httpx.HTTPError, httpx.TimeoutException):
            return google_url

    async def _aresolve_url(self, google_url: str, client: httpx.AsyncClient) -> str:
        """Async twin of _resolve_url."""
        if _is_google_news(google_url):
            try:
                found = await self._adecode(google_url, client)
            except Exception:  # undocumented endpoint; any break drops to the HEAD fallback
                found = ""
            if found:
                return found
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
        return _browser_pass(results)

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
        return await asyncio.to_thread(_browser_pass, results)

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
