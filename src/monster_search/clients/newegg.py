"""Newegg product search via Crawl4AI scraping."""

from __future__ import annotations

import re
from urllib.parse import quote_plus

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

# ---------------------------------------------------------------------------
# Primary pattern: product image links with title in alt text
# Matches: [![Product Title](image-url)Quick View](product-url)
# The actual product name lives in the image alt text, not in standalone links.
# ---------------------------------------------------------------------------
_IMG_PRODUCT_RE = re.compile(
    r"\[!\["                          # opening [![
    r"([^\]]{10,300})"                # (1) product title from image alt text
    r"\]\([^\)]+\)"                   # ](image-url)
    r"[^\]]*"                         # optional trailing text like "Quick View"
    r"\]\("                           # ](
    r"(https://www\.newegg\.com/[^\s)]+)"  # (2) product URL
    r"\)",                            # closing )
    re.IGNORECASE,
)

# Fallback pattern: plain markdown links to Newegg product pages
# Matches: [Product Title](https://www.newegg.com/... )
_PRODUCT_LINK_RE = re.compile(
    r"\[([^\]]{10,300})\]"  # title in brackets (10-300 chars, skip tiny nav links)
    r"\((https://www\.newegg\.com/[^\s)]+)\)",  # URL in parens
    re.IGNORECASE,
)

# Titles that indicate non-product links (UI elements, price ranges, etc.)
_JUNK_TITLE_RE = re.compile(
    r"^(more options from|add to cart|view details|see details|quick view|compare|lowest price)",
    re.IGNORECASE,
)

# Price pattern: $X,XXX.XX or $XXX.XX
_PRICE_RE = re.compile(r"\$[\d,]+\.?\d*")

# Bold-price pattern from Newegg listings: **649**.99 or **1,649**.99
_BOLD_PRICE_RE = re.compile(r"\*\*([\d,]+)\*\*\.(\d{2})")


def _build_search_url(query: str) -> str:
    """Build a Newegg search URL sorted by best match."""
    return f"https://www.newegg.com/p/pl?d={quote_plus(query)}&Order=1"


def _build_crawl_payload(url: str) -> dict:
    """Build the Crawl4AI POST payload for a Newegg search page."""
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


def _extract_markdown(data: dict) -> str:
    """Extract raw markdown text from a Crawl4AI response."""
    results_data = data.get("results", [])
    if not results_data:
        return ""
    first = results_data[0]
    md_field = first.get("markdown", "")
    if isinstance(md_field, dict):
        return md_field.get("raw_markdown", "") or md_field.get("fit_markdown", "")
    return md_field or ""


def _is_product_url(url: str) -> bool:
    """Filter URLs that look like actual product pages, not navigation/category."""
    # Product URLs contain /p/ with an item ID, or product detail patterns
    # Exclude category/navigation pages
    lower = url.lower()
    if "/p/pl?" in lower:
        # This is a search/listing page, not a product
        return False
    if "/p/" in lower or "/product/" in lower or "-p-" in lower:
        return True
    # Newegg item IDs are typically like N82E16835XXX or 9SIXXXX patterns
    if re.search(r"/[\dA-Z]{10,}", url):
        return True
    return False


def _extract_price_near(markdown: str, start: int, window: int = 800) -> str | None:
    """Find the best price in a window after *start*.

    Prefer the Newegg bold-price format (``**649**.99``) which is the actual
    selling price.  Fall back to a plain ``$X.XX`` pattern.

    The window defaults to 800 chars because on real Newegg pages the price
    section appears after brand image, rating, "View Details", and model
    number lines — typically 500-600 chars from the product image link.
    """
    context = markdown[start : start + window]
    # Newegg renders the current price as **digits**.digits
    bold = _BOLD_PRICE_RE.search(context)
    if bold:
        return f"${bold.group(1)}.{bold.group(2)}"
    # Fallback: plain dollar amount
    plain = _PRICE_RE.search(context)
    return plain.group(0) if plain else None


def _clean_url(url: str) -> str:
    """Strip Newegg fragment anchors (e.g. #moreBuyOptions) from URLs."""
    idx = url.find("#")
    return url[:idx] if idx != -1 else url


def _parse_products(markdown: str, max_results: int) -> list[SearchResult]:
    """Parse Newegg product listings from Crawl4AI markdown output.

    Strategy (in priority order):
    1. Image-alt-text links  ``[![Title](img)Quick View](url)``  — these
       contain the real product name on live Newegg pages.
    2. Fallback to plain ``[Title](url)`` links for simpler markdown (e.g.
       test fixtures) while filtering out junk titles like
       "More options from …".
    """
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    # --- Pass 1: image-alt-text links (preferred, from real Crawl4AI) ------
    for match in _IMG_PRODUCT_RE.finditer(markdown):
        if len(results) >= max_results:
            break

        title = match.group(1).strip()
        url = _clean_url(match.group(2).strip())

        if not _is_product_url(url):
            continue
        if url in seen_urls:
            continue
        if _JUNK_TITLE_RE.search(title):
            continue
        seen_urls.add(url)

        price = _extract_price_near(markdown, match.end())

        # Snippet: model number or shipping info after the product block
        snippet = _build_snippet(markdown, match.start(), match.end())

        results.append(
            SearchResult(
                title=title,
                url=url,
                snippet=snippet,
                source="newegg",
                price=price,
                in_stock=None,
            )
        )

    # --- Pass 2: plain links (fallback for simpler markdown) ---------------
    if len(results) < max_results:
        for match in _PRODUCT_LINK_RE.finditer(markdown):
            if len(results) >= max_results:
                break

            title = match.group(1).strip()
            url = _clean_url(match.group(2).strip())

            if not _is_product_url(url):
                continue
            if url in seen_urls:
                continue
            # Filter junk titles
            if _JUNK_TITLE_RE.search(title):
                continue
            if len(title) < 15:
                continue
            seen_urls.add(url)

            price = _extract_price_near(markdown, match.end())
            snippet = _build_snippet(markdown, match.start(), match.end())

            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="newegg",
                    price=price,
                    in_stock=None,
                )
            )

    return results


def _build_snippet(markdown: str, match_start: int, match_end: int) -> str:
    """Build a readable snippet from context around a product match."""
    post_context = markdown[match_end : match_end + 800]
    post_lines = [ln.strip() for ln in post_context.split("\n") if ln.strip()]

    # Collect useful snippet parts: model number, shipping info, etc.
    parts: list[str] = []
    for line in post_lines:
        cleaned = line.lstrip("* ").strip()
        # Skip lines that are just markdown links/images
        if not cleaned or cleaned.startswith("[") or cleaned.startswith("!"):
            continue
        # Skip price-only lines (digits, dollar signs, bold markers)
        if re.match(r"^[\$\d,.*\s–-]+$", cleaned):
            continue
        # Skip UI junk
        lower = cleaned.lower()
        if lower in ("add to cart", "compare", "quick view"):
            continue
        # Strip residual bold markers from markdown
        cleaned = cleaned.replace("**", "")
        parts.append(cleaned)
        if len(parts) >= 2:
            break

    snippet = " | ".join(parts) if parts else ""

    # If nothing useful in post-context, try pre-context
    if len(snippet) < 10:
        pre_start = max(0, match_start - 200)
        pre_context = markdown[pre_start:match_start].strip()
        pre_lines = [ln.strip() for ln in pre_context.split("\n") if ln.strip()]
        for line in reversed(pre_lines):
            cleaned = line.lstrip("* ").strip()
            if cleaned and not cleaned.startswith("[") and not cleaned.startswith("!"):
                lower = cleaned.lower()
                if lower not in ("add to cart", "compare", "quick view"):
                    snippet = cleaned
                    break

    if len(snippet) > 500:
        snippet = snippet[:500]
    return snippet


class NeweggClient:
    """Search Newegg product listings via Crawl4AI page scraping."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _crawl4ai_url(self) -> str:
        return self._config.crawl4ai_url

    def _timeout(self) -> int:
        return self._config.newegg_timeout

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous Newegg product search via Crawl4AI."""
        max_results = max_results or self._config.max_results
        search_url = _build_search_url(query)
        payload = _build_crawl_payload(search_url)
        base_url = self._crawl4ai_url()
        timeout = self._timeout()

        client = get_client(base_url, timeout)
        resp = client.post(f"{base_url}/crawl", json=payload)
        resp.raise_for_status()

        markdown = _extract_markdown(resp.json())
        return _parse_products(markdown, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async Newegg product search via Crawl4AI."""
        max_results = max_results or self._config.max_results
        search_url = _build_search_url(query)
        payload = _build_crawl_payload(search_url)
        base_url = self._crawl4ai_url()
        timeout = self._timeout()

        client = get_async_client(base_url, timeout)
        resp = await client.post(f"{base_url}/crawl", json=payload)
        resp.raise_for_status()

        markdown = _extract_markdown(resp.json())
        return _parse_products(markdown, max_results)
