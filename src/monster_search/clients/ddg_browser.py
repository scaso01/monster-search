"""DuckDuckGo via Crawl4AI's headless browser.

Why this exists: DuckDuckGo and Brave fingerprint the TLS handshake, not the
exit address. A plain HTTP client (httpx, curl, SearXNG's Python stack) gets
403/429 from every exit we own -- NordVPN, Oracle, either machine. The same
request from a real browser succeeds. Measured 2026-08-17: curl 403, Crawl4AI
12 results in 2.8s, from the identical address.

So this client asks Crawl4AI (Playwright/Chromium on Monster) to load the
lightweight HTML endpoint and extract the result rows server-side, which keeps
HTML parsing -- and any parsing dependency -- out of this codebase.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, quote, urlparse

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

# html.duckduckgo.com markup is plain and stable (result__a / result__snippet),
# unlike Brave's Svelte build whose class hashes change every deploy.
EXTRACTION_SCHEMA = {
    "name": "ddg_results",
    "baseSelector": "div.result",
    "fields": [
        {"name": "title", "selector": "a.result__a", "type": "text"},
        {"name": "url", "selector": "a.result__a", "type": "attribute", "attribute": "href"},
        {"name": "snippet", "selector": "a.result__snippet", "type": "text"},
    ],
}

# Ad rows ride in the same div.result container as organic ones and point at
# DDG's click tracker rather than a destination.
_AD_MARKERS = ("y.js", "ad_provider=", "ad_domain=")


def unwrap_url(href: str) -> str:
    """Resolve DuckDuckGo's `//duckduckgo.com/l/?uddg=<encoded>` redirect.

    Returns the destination, or the original href when it isn't a redirect.
    """
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" not in href:
        return href
    target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
    return target or href


def is_ad(href: str) -> bool:
    """True when the row is a paid placement rather than an organic result."""
    return any(marker in href for marker in _AD_MARKERS)


class DdgBrowserClient:
    """DuckDuckGo search driven through the Crawl4AI browser service."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _build_payload(self, query: str) -> dict:
        return {
            "urls": [SEARCH_URL.format(query=quote(query))],
            "browser_config": {"type": "BrowserConfig", "params": {"headless": True}},
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {
                    "cache_mode": "bypass",
                    "extraction_strategy": {
                        "type": "JsonCssExtractionStrategy",
                        "params": {"schema": EXTRACTION_SCHEMA},
                    },
                },
            },
        }

    def _parse_response(self, data: dict, max_results: int) -> list[SearchResult]:
        results_data = data.get("results") or []
        if not results_data:
            return []
        first = results_data[0]
        if not first.get("success"):
            return []

        raw = first.get("extracted_content") or "[]"
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            # A layout change makes extraction return prose instead of JSON.
            # Empty beats a half-parsed result set; the caller's other engines
            # still answer, and the smoke test fails loudly.
            return []

        out: list[SearchResult] = []
        for item in items:
            href = item.get("url", "")
            if is_ad(href):
                continue
            url = unwrap_url(href)
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue
            out.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=(item.get("snippet") or "").strip(),
                    source="ddg",
                )
            )
            if len(out) >= max_results:
                break
        return out

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous DuckDuckGo search through the browser service."""
        max_results = max_results or self._config.max_results
        client = get_client(self._config.crawl4ai_url, self._config.crawl4ai_timeout)
        resp = client.post(f"{self._config.crawl4ai_url}/crawl", json=self._build_payload(query))
        resp.raise_for_status()
        return self._parse_response(resp.json(), max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async DuckDuckGo search through the browser service."""
        max_results = max_results or self._config.max_results
        client = get_async_client(self._config.crawl4ai_url, self._config.crawl4ai_timeout)
        resp = await client.post(
            f"{self._config.crawl4ai_url}/crawl", json=self._build_payload(query)
        )
        resp.raise_for_status()
        return self._parse_response(resp.json(), max_results)
