"""Amazon Deals client via Crawl4AI page scraping."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_GOLDBOX_URL = "https://www.amazon.com/gp/goldbox"
_SEARCH_URL = "https://www.amazon.com/s"

# Regex patterns for parsing Amazon markdown output
# Amazon product URLs: /dp/ASIN or /gp/product/ASIN
_ASIN_URL_RE = re.compile(
    r"https?://(?:www\.)?amazon\.com/(?:[^\s\)]*?/)?(?:dp|gp/product)/([A-Z0-9]{10})[^\s\)]*"
)
# Price pattern: $XX.XX or $X,XXX.XX
_PRICE_RE = re.compile(r"\$[\d,]+\.\d{2}")
# Discount pattern: XX% off
_DISCOUNT_RE = re.compile(r"(\d{1,3})%\s*off", re.IGNORECASE)


def _normalize_url(url: str) -> str:
    """Strip tracking params, return clean Amazon product URL."""
    match = _ASIN_URL_RE.search(url)
    if match:
        asin = match.group(1)
        return f"https://www.amazon.com/dp/{asin}"
    return url


def _extract_deals_from_markdown(markdown: str, query: str, max_results: int) -> list[SearchResult]:
    """Parse Crawl4AI markdown output for Amazon deal items.

    Strategy: split markdown into chunks around product URLs,
    then extract title, price, and discount from surrounding text.
    """
    if not markdown or not markdown.strip():
        return []

    query_words = query.lower().split() if query else []
    results: list[SearchResult] = []
    seen_asins: set[str] = set()

    # Find all Amazon product URLs in the markdown
    for match in _ASIN_URL_RE.finditer(markdown):
        if len(results) >= max_results:
            break

        asin = match.group(1)
        if asin in seen_asins:
            continue
        seen_asins.add(asin)

        raw_url = match.group(0)
        clean_url = _normalize_url(raw_url)

        # Extract context: 500 chars before and after the URL
        start = max(0, match.start() - 500)
        end = min(len(markdown), match.end() + 500)
        context = markdown[start:end]

        # Extract title: look for markdown link text [title](url) or nearby heading/bold text
        title = _extract_title(context, raw_url)

        # Extract price
        prices = _PRICE_RE.findall(context)
        price = prices[0] if prices else None

        # Extract discount
        discount_match = _DISCOUNT_RE.search(context)
        discount = f"{discount_match.group(1)}% off" if discount_match else None

        # Filter by query keywords if a query was provided.
        # Use title + URL path (contains product keywords) — NOT the wide context
        # window, which bleeds text from neighboring products.
        if query_words:
            searchable = f"{title} {raw_url}".lower()
            if not any(word in searchable for word in query_words):
                continue

        # Build snippet
        snippet_parts: list[str] = []
        if discount:
            snippet_parts.append(discount)
        if price and len(prices) > 1:
            snippet_parts.append(f"Sale: {prices[0]} (was {prices[1]})")
        elif price:
            snippet_parts.append(f"Price: {price}")
        if not snippet_parts:
            snippet_parts.append("Amazon Deal")

        results.append(SearchResult(
            title=title or f"Amazon Deal ({asin})",
            url=clean_url,
            snippet=" | ".join(snippet_parts),
            source="amazon_deals",
            price=price,
            in_stock=True,
        ))

    return results


def _extract_title(context: str, url: str) -> str:
    """Extract a product title from markdown context around a URL.

    Looks for:
    1. Markdown link text: [Product Title](url)
    2. Markdown heading before URL: ## Product Title
    3. Bold text before URL: **Product Title**
    4. First non-empty line near the URL
    """
    # Try markdown link: [title](url)
    escaped_url = re.escape(url)
    # Truncate to keep the regex fast, but avoid cutting mid-escape sequence
    # (e.g. slicing "\\." to just "\\" produces "bad escape (end of pattern)")
    truncated = escaped_url[:60]
    if truncated.endswith("\\"):
        truncated = truncated[:-1]
    link_re = re.compile(r"\[([^\]]{3,200})\]\(" + truncated)
    link_match = link_re.search(context)
    if link_match:
        return link_match.group(1).strip()

    # Try any markdown link near the URL
    any_link_re = re.compile(r"\[([^\]]{3,200})\]\(https?://[^\)]*amazon\.com[^\)]*\)")
    any_link_match = any_link_re.search(context)
    if any_link_match:
        return any_link_match.group(1).strip()

    # Try bold text: **title**
    bold_re = re.compile(r"\*\*([^*]{3,200})\*\*")
    bold_match = bold_re.search(context)
    if bold_match:
        return bold_match.group(1).strip()

    # Try heading: ## title
    heading_re = re.compile(r"^#{1,4}\s+(.{3,200})", re.MULTILINE)
    heading_match = heading_re.search(context)
    if heading_match:
        return heading_match.group(1).strip()

    return ""


class AmazonDealsClient:
    """Search Amazon deals via Crawl4AI page scraping."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @property
    def _crawl4ai_url(self) -> str:
        return self._config.crawl4ai_url

    @property
    def _timeout(self) -> int:
        return self._config.amazon_deals_timeout

    def _build_crawl_payload(self, url: str) -> dict:
        """Build Crawl4AI POST payload for the given URL."""
        return {
            "urls": [url],
            "browser_config": {
                "type": "BrowserConfig",
                "params": {"headless": True},
            },
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": {"cache_mode": "bypass"},
            },
        }

    def _target_url(self, query: str) -> str:
        """Build the Amazon URL to scrape based on the query."""
        if not query or not query.strip():
            return _GOLDBOX_URL
        return f"{_SEARCH_URL}?k={quote_plus(query)}&s=price-asc-rank"

    @staticmethod
    def _extract_markdown(data: dict) -> str:
        """Extract markdown text from Crawl4AI response."""
        results_data = data.get("results", [])
        if not results_data:
            return ""
        first = results_data[0]
        md_field = first.get("markdown", "")
        if isinstance(md_field, dict):
            return md_field.get("raw_markdown", "") or md_field.get("fit_markdown", "")
        return md_field or ""

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search for Amazon deals via Crawl4AI."""
        max_results = max_results or self._config.max_results
        url = self._target_url(query)
        payload = self._build_crawl_payload(url)
        client = get_client(self._crawl4ai_url, self._timeout)
        resp = client.post(f"{self._crawl4ai_url}/crawl", json=payload)
        resp.raise_for_status()
        markdown = self._extract_markdown(resp.json())
        return _extract_deals_from_markdown(markdown, query, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search for Amazon deals via Crawl4AI."""
        max_results = max_results or self._config.max_results
        url = self._target_url(query)
        payload = self._build_crawl_payload(url)
        client = get_async_client(self._crawl4ai_url, self._timeout)
        resp = await client.post(f"{self._crawl4ai_url}/crawl", json=payload)
        resp.raise_for_status()
        markdown = self._extract_markdown(resp.json())
        return _extract_deals_from_markdown(markdown, query, max_results)
