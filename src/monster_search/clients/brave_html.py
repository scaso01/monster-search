"""Brave Search via its HTML page, as a last-resort web engine.

Brave rejects httpx by TLS fingerprint (same IP, same moment: curl 200, httpx 429),
so this uses curl_cffi impersonating Chrome. An exit can be blocked after one query
with a ~36 minute 429, so callers should use it only as a fallback.
"""

from __future__ import annotations

import html
import re

from monster_search.config import Config
from monster_search.models import SearchResult

SEARCH_URL = "https://search.brave.com/search"
TIMEOUT_S = 20

_BLOCK = re.compile(r'<div class="snippet[^"]*"[^>]*data-type="web"')
_HREF = re.compile(r'<a href="(https?://[^"]+)"')
_TITLE = re.compile(r'class="title search-snippet-title[^"]*"[^>]*title="([^"]*)"')
_SNIPPET = re.compile(r'<div class="generic-snippet[^"]*">(.*?)</div>\s*<!--', re.S)
_DATE_PREFIX = re.compile(r'<span class="t-secondary">[^<]*</span>')
_TAGS = re.compile(r"<[^>]+>")


class BraveBlockedError(RuntimeError):
    """Every exit answered 429; Brave's block lasts about 36 minutes."""


def parse_results(page: str, max_results: int = 10) -> list[SearchResult]:
    starts = [m.start() for m in _BLOCK.finditer(page)]
    results: list[SearchResult] = []
    for i, start in enumerate(starts):
        block = page[start : starts[i + 1] if i + 1 < len(starts) else len(page)]
        href = _HREF.search(block)
        if not href:
            continue
        title = _TITLE.search(block)
        snippet = _SNIPPET.search(block)
        text = ""
        if snippet:
            text = _TAGS.sub("", _DATE_PREFIX.sub("", snippet.group(1)))
            text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        results.append(
            SearchResult(
                title=html.unescape(title.group(1)) if title else "",
                url=html.unescape(href.group(1)),
                snippet=text,
                source="brave",
                engine="brave",
            )
        )
        if len(results) >= max_results:
            break
    return results


class BraveHtmlClient:
    def __init__(self, config: Config | None = None) -> None:
        self.config = config or Config()

    def _exits(self) -> list[str | None]:
        # socks5h so DNS resolves at the exit, not locally.
        exits: list[str | None] = [
            p.replace("socks5://", "socks5h://", 1) for p in self.config.socks_proxies
        ]
        return exits or [None]

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        from curl_cffi import requests  # optional dependency; ImportError tells the caller

        blocked: list[str] = []
        for proxy in self._exits():
            resp = requests.get(
                SEARCH_URL,
                params={"q": query, "source": "web"},
                impersonate="chrome",
                proxy=proxy,
                timeout=TIMEOUT_S,
            )
            if resp.status_code == 429:
                blocked.append(proxy or "direct")
                continue  # this exit is blocked for ~36 min; retrying it only extends that
            resp.raise_for_status()
            return parse_results(resp.text, max_results)
        raise BraveBlockedError(f"Brave returned 429 on every exit ({len(blocked)})")
