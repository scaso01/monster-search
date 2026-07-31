"""Perplexity AI synthesis client (cookie-based free account).

Uses curl_cffi for TLS fingerprinting to bypass Cloudflare, then hits
Perplexity's internal SSE API directly. No third-party scraper library needed.
"""

from __future__ import annotations

import json

from monster_search.config import Config
from monster_search.models import SearchResult

_API_BASE = "https://www.perplexity.ai"
_SESSION_COOKIE = "__Secure-next-auth.session-token"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
    "Origin": _API_BASE,
    "Referer": f"{_API_BASE}/",
}


class PerplexityClient:
    """Client for Perplexity AI web search synthesis."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _token_from_browser(self) -> str | None:
        """Read the live Perplexity session cookie from a local browser profile.

        This is the self-healing option: as long as you are logged into
        perplexity.ai in the configured browser, the token is always current and
        no static secret is stored in .env. It reuses yt-dlp's cookie extractor,
        which is already a dependency. Returns None when not configured, the
        browser or cookie is not found, or extraction fails, so the caller falls
        back to the static env-var token.
        """
        spec = self._config.perplexity_cookies_from_browser
        if not spec:
            return None
        try:
            from yt_dlp.cookies import YDLLogger, extract_cookies_from_browser
        except ImportError:
            return None
        parts = spec.split(":", 1)
        browser = parts[0].strip().lower()
        profile = parts[1].strip() if len(parts) > 1 else None
        try:
            jar = extract_cookies_from_browser(browser, profile, YDLLogger())
        except Exception:
            return None
        for cookie in jar:
            if cookie.name == _SESSION_COOKIE and "perplexity" in (cookie.domain or ""):
                return cookie.value or None
        return None

    def _parse_sse(self, text: str) -> tuple[str, list[SearchResult]]:
        """Parse SSE response into answer + sources."""
        answer = ""
        sources: list[SearchResult] = []
        seen_urls: set[str] = set()

        for line in text.split("\n"):
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            if data.get("status") == "FAILED":
                raise RuntimeError(
                    f"Perplexity query failed: {data.get('text', 'Unknown error')}"
                )

            raw_text = data.get("text", "")
            if not raw_text:
                continue

            # Response is JSON array of steps
            try:
                steps = json.loads(raw_text)
            except json.JSONDecodeError:
                continue
            if not isinstance(steps, list):
                continue

            for step in steps:
                step_type = step.get("step_type")
                content = step.get("content", {})

                if step_type == "FINAL":
                    inner = content.get("answer", "")
                    if not inner:
                        continue
                    try:
                        parsed = json.loads(inner)
                        answer = parsed.get("answer", "")
                        for ref in parsed.get("web_results", []):
                            url = ref.get("url", "")
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                sources.append(SearchResult(
                                    title=ref.get("name", ""),
                                    url=url,
                                    snippet=ref.get("snippet", ""),
                                    source="perplexity",
                                ))
                    except json.JSONDecodeError:
                        answer = inner

                elif step_type == "SEARCH_RESULTS":
                    for r in content.get("results", []):
                        url = r.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            sources.append(SearchResult(
                                title=r.get("name", ""),
                                url=url,
                                snippet=r.get("snippet", ""),
                                source="perplexity",
                            ))

        if not answer and not sources:
            # The request succeeded and the stream contained nothing usable,
            # which in practice means an expired session cookie or a changed
            # response shape rather than a query with no answer. Returning an
            # empty answer here reads as success and leaves the user staring at
            # a blank result, so this reports failure and lets the tiered search
            # mark the engine down and use the others.
            raise RuntimeError(
                "Perplexity returned no answer and no sources. The session "
                "cookie has most likely expired: log in to perplexity.ai again "
                "in the browser named by MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER, "
                "or refresh MONSTER_PERPLEXITY_SESSION_TOKEN."
            )

        return answer, sources

    def search(self, query: str) -> tuple[str, list[SearchResult]]:
        """Search via Perplexity using session cookie.

        Token resolution order:
        1. Live cookie from the configured local browser profile
           (MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER) — self-heals on every
           login, so no long-lived secret sits on disk.
        2. Static MONSTER_PERPLEXITY_SESSION_TOKEN env var (fallback).
        """
        token = self._token_from_browser() or self._config.perplexity_session_token
        if not token:
            raise ValueError(
                "Perplexity session token required. Either set "
                "MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER (e.g. 'firefox') to read the live "
                "cookie from your logged-in browser, or set MONSTER_PERPLEXITY_SESSION_TOKEN "
                "with the __Secure-next-auth.session-token cookie from perplexity.ai"
            )

        try:
            from curl_cffi.requests import Session
        except ImportError:
            raise ImportError(
                "curl_cffi not installed. Install with: pip install curl_cffi"
            )

        cookies = {_SESSION_COOKIE: token}

        with Session(
            impersonate="chrome",
            cookies=cookies,
            headers=_HEADERS,
            timeout=self._config.perplexity_timeout,
        ) as session:
            # Init search session (required by Perplexity backend)
            session.get(f"{_API_BASE}/search/new", params={"q": query[:500]})

            # Query
            payload = {
                "query_str": query,
                "params": {
                    "version": "2.18",
                    "source": "default",
                    "language": "en-US",
                    "search_focus": "internet",
                    "mode": "concise",
                },
            }
            resp = session.post(f"{_API_BASE}/rest/sse/perplexity_ask", json=payload)
            if resp.status_code == 403:
                raise ValueError(
                    "Perplexity auth failed (403). Session expired or logged out. "
                    "If using MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER, just log back into "
                    "perplexity.ai in that browser. Otherwise refresh "
                    "MONSTER_PERPLEXITY_SESSION_TOKEN from F12 > Application > Cookies > "
                    "__Secure-next-auth.session-token"
                )
            resp.raise_for_status()
            return self._parse_sse(resp.text)

    async def asearch(self, query: str) -> tuple[str, list[SearchResult]]:
        """Async wrapper — curl_cffi is sync-only."""
        import asyncio
        return await asyncio.to_thread(self.search, query)
