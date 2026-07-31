"""Crawl4AI page content extraction client."""

from __future__ import annotations


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class Crawl4AIClient:
    """Client for Crawl4AI JS-rendered page extraction."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _build_payload(self, url: str, *, wait_for: str | None = None) -> dict:
        crawler_params: dict = {"cache_mode": "bypass"}
        if wait_for:
            crawler_params["wait_for"] = wait_for
        return {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": crawler_params,
            },
        }

    def _parse_response(self, data: dict, url: str) -> tuple[str, list[SearchResult]]:
        results_data = data.get("results", [])
        if not results_data:
            return "", []
        first = results_data[0]
        md_field = first.get("markdown", "")
        if isinstance(md_field, dict):
            markdown = md_field.get("raw_markdown", "") or md_field.get("fit_markdown", "")
        else:
            markdown = md_field or ""
        markdown = markdown or first.get("html", "")
        result = SearchResult(
            title=first.get("metadata", {}).get("title", url),
            url=url,
            snippet=markdown[:200] if markdown else "",
            source="crawl4ai",
        )
        return markdown, [result]

    def search(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Extract content from a URL via Crawl4AI."""
        payload = self._build_payload(url, wait_for=wait_for)
        effective_timeout = timeout or self._config.crawl4ai_timeout
        client = get_client(self._config.crawl4ai_url, effective_timeout)
        resp = client.post(f"{self._config.crawl4ai_url}/crawl", json=payload)
        resp.raise_for_status()
        return self._parse_response(resp.json(), url)

    async def asearch(
        self,
        url: str,
        *,
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Async extract content from a URL via Crawl4AI."""
        payload = self._build_payload(url, wait_for=wait_for)
        effective_timeout = timeout or self._config.crawl4ai_timeout
        client = get_async_client(self._config.crawl4ai_url, effective_timeout)
        resp = await client.post(f"{self._config.crawl4ai_url}/crawl", json=payload)
        resp.raise_for_status()
        return self._parse_response(resp.json(), url)
