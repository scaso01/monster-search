"""Tests for Amazon Deals client via Crawl4AI scraping."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.amazon_deals import (
    AmazonDealsClient,
    _extract_deals_from_markdown,
    _extract_title,
    _normalize_url,
)
from monster_search.models import SearchResult

# ---------------------------------------------------------------------------
# Realistic markdown that Crawl4AI might return from an Amazon deals page
# ---------------------------------------------------------------------------
AMAZON_DEALS_MARKDOWN = """\
# Today's Deals

## Featured Deals

[Sony WH-1000XM5 Wireless Noise Canceling Headphones](https://www.amazon.com/Sony-WH-1000XM5-Canceling-Headphones-Phone-Call/dp/B09XS7JWHH/ref=sr_1_1)

**45% off** | ~~$399.99~~ $219.99

Limited time deal

---

[Samsung 980 PRO SSD 2TB NVMe M.2 Internal Solid State Drive](https://www.amazon.com/Samsung-Internal-Solid-State-MZ-V8P2T0B/dp/B08RK2SR23/ref=sr_1_2)

**30% off** | ~~$249.99~~ $174.99

Top deal - Ends today

---

[Apple AirPods Pro (2nd Generation)](https://www.amazon.com/Apple-Generation-Cancelling-Transparency-Personalized/dp/B0D1XD1ZV3/ref=sr_1_3)

$189.99

Best seller in Electronics

---

[Anker PowerCore 26800mAh Portable Charger](https://www.amazon.com/Anker-Portable-Charger-PowerCore-26800mAh/dp/B01JIWQPMW/ref=sr_1_4)

**20% off** | ~~$65.99~~ $52.79

Deal of the day

---

Some random text without any product links here.
This should be ignored by the parser.
"""

# Markdown with no products at all
EMPTY_MARKDOWN = """\
# Amazon.com

Sign in to see your deals.

No deals available at this time.
"""

# Response wrapper matching Crawl4AI format
CRAWL4AI_RESPONSE = {
    "results": [
        {
            "url": "https://www.amazon.com/gp/goldbox",
            "markdown": AMAZON_DEALS_MARKDOWN,
            "metadata": {"title": "Today's Deals"},
        }
    ]
}

CRAWL4AI_RESPONSE_DICT_MARKDOWN = {
    "results": [
        {
            "url": "https://www.amazon.com/gp/goldbox",
            "markdown": {
                "raw_markdown": AMAZON_DEALS_MARKDOWN,
                "fit_markdown": "",
            },
            "metadata": {"title": "Today's Deals"},
        }
    ]
}

CRAWL4AI_EMPTY_RESPONSE = {
    "results": [
        {
            "url": "https://www.amazon.com/gp/goldbox",
            "markdown": EMPTY_MARKDOWN,
            "metadata": {"title": "Today's Deals"},
        }
    ]
}

CRAWL4AI_NO_RESULTS = {"results": []}

CRAWL4AI_URL = "http://localhost:11235"


# ---------------------------------------------------------------------------
# Unit tests — parsing logic
# ---------------------------------------------------------------------------
class TestNormalizeUrl:
    def test_dp_url(self):
        url = "https://www.amazon.com/Sony-Headphones/dp/B09XS7JWHH/ref=sr_1_1"
        assert _normalize_url(url) == "https://www.amazon.com/dp/B09XS7JWHH"

    def test_gp_product_url(self):
        url = "https://www.amazon.com/gp/product/B08RK2SR23/ref=xyz"
        assert _normalize_url(url) == "https://www.amazon.com/dp/B08RK2SR23"

    def test_non_amazon_url(self):
        url = "https://example.com/something"
        assert _normalize_url(url) == "https://example.com/something"

    def test_amazon_non_product_url(self):
        url = "https://www.amazon.com/gp/goldbox"
        assert _normalize_url(url) == "https://www.amazon.com/gp/goldbox"


class TestExtractTitle:
    def test_markdown_link(self):
        context = "[Great Product Name](https://www.amazon.com/dp/B09XS7JWHH)"
        url = "https://www.amazon.com/dp/B09XS7JWHH"
        assert _extract_title(context, url) == "Great Product Name"

    def test_bold_text(self):
        context = "Some text **Awesome Gadget 3000** more text"
        url = "https://www.amazon.com/dp/B09XS7JWHH"
        assert _extract_title(context, url) == "Awesome Gadget 3000"

    def test_heading(self):
        context = "## Best Deal Ever\nSome description"
        url = "https://www.amazon.com/dp/NOTINTEXT1"
        assert _extract_title(context, url) == "Best Deal Ever"

    def test_empty_context(self):
        assert _extract_title("", "https://www.amazon.com/dp/B09XS7JWHH") == ""

    def test_url_with_dot_at_truncation_boundary(self):
        """Regression: escaped URL truncated to 60 chars could split a '\\.', leaving
        a trailing backslash that causes 'bad escape (end of pattern)'."""
        # Craft a URL whose re.escape output has '\\.' at exactly the 59-60 boundary
        url = "https://www.amazon.com/" + "A" * 34 + "./dp/B09XS7JWHH"
        context = f"[Great Product]({url}) more text"
        # Should not raise re.error
        title = _extract_title(context, url)
        assert isinstance(title, str)


class TestExtractDealsFromMarkdown:
    def test_extracts_multiple_products(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        assert len(results) == 4

    def test_respects_max_results(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 2)
        assert len(results) == 2

    def test_extracts_title(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        assert "Sony WH-1000XM5" in results[0].title

    def test_extracts_price(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        # First product has two prices; sale price ($219.99) should be captured
        assert results[0].price is not None
        assert "$" in results[0].price

    def test_extracts_discount(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        # First product: 45% off
        assert "45% off" in results[0].snippet

    def test_clean_urls(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        for r in results:
            assert r.url.startswith("https://www.amazon.com/dp/")
            assert "ref=" not in r.url

    def test_deduplicates_asins(self):
        """Same ASIN appearing twice should only produce one result."""
        doubled = AMAZON_DEALS_MARKDOWN + "\n\n" + AMAZON_DEALS_MARKDOWN
        results = _extract_deals_from_markdown(doubled, "", 20)
        asins = [r.url.split("/dp/")[1] for r in results]
        assert len(asins) == len(set(asins))

    def test_source_is_amazon_deals(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        for r in results:
            assert r.source == "amazon_deals"

    def test_in_stock_true(self):
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "", 10)
        for r in results:
            assert r.in_stock is True

    def test_query_filters(self):
        """Only products matching query keywords should be returned."""
        results = _extract_deals_from_markdown(AMAZON_DEALS_MARKDOWN, "sony headphones", 10)
        assert len(results) >= 1
        assert any("Sony" in r.title for r in results)
        # Non-matching products should be filtered out
        assert not any("Samsung" in r.title for r in results)

    def test_empty_markdown(self):
        results = _extract_deals_from_markdown("", "test", 10)
        assert results == []

    def test_no_products_markdown(self):
        results = _extract_deals_from_markdown(EMPTY_MARKDOWN, "", 10)
        assert results == []

    def test_whitespace_only_markdown(self):
        results = _extract_deals_from_markdown("   \n\n  ", "test", 10)
        assert results == []


# ---------------------------------------------------------------------------
# Integration-style tests — mock Crawl4AI HTTP endpoint
# ---------------------------------------------------------------------------
class TestAmazonDealsClientSync:
    @respx.mock
    def test_search_returns_results(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_RESPONSE)
        )
        client = AmazonDealsClient()
        results = client.search("headphones", max_results=5)

        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)

    @respx.mock
    def test_search_sends_correct_payload(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_RESPONSE)
        )
        client = AmazonDealsClient()
        client.search("laptop", max_results=5)

        request = respx.calls[0].request
        import json

        body = json.loads(request.content)
        assert "urls" in body
        assert len(body["urls"]) == 1
        assert "laptop" in body["urls"][0]
        assert "amazon.com" in body["urls"][0]
        assert body["browser_config"]["params"]["headless"] is True

    @respx.mock
    def test_search_empty_query_uses_goldbox(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_RESPONSE)
        )
        client = AmazonDealsClient()
        client.search("", max_results=5)

        request = respx.calls[0].request
        import json

        body = json.loads(request.content)
        assert body["urls"][0] == "https://www.amazon.com/gp/goldbox"

    @respx.mock
    def test_search_no_results_from_empty_markdown(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_EMPTY_RESPONSE)
        )
        client = AmazonDealsClient()
        results = client.search("xyznonexistent123")
        assert results == []

    @respx.mock
    def test_search_no_crawl4ai_results(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_NO_RESULTS)
        )
        client = AmazonDealsClient()
        results = client.search("test")
        assert results == []

    @respx.mock
    def test_search_dict_markdown_format(self):
        """Crawl4AI sometimes returns markdown as a dict with raw_markdown key."""
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_RESPONSE_DICT_MARKDOWN)
        )
        client = AmazonDealsClient()
        results = client.search("headphones", max_results=5)
        assert len(results) >= 1

    @respx.mock
    def test_http_error_raises(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(500)
        )
        client = AmazonDealsClient()
        with pytest.raises(httpx.HTTPStatusError):
            client.search("test")

    @respx.mock
    def test_timeout_raises(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        client = AmazonDealsClient()
        with pytest.raises(httpx.TimeoutException):
            client.search("test")


class TestAmazonDealsClientAsync:
    @respx.mock
    @pytest.mark.asyncio
    async def test_async_search_returns_results(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_RESPONSE)
        )
        client = AmazonDealsClient()
        results = await client.asearch("headphones", max_results=5)

        assert len(results) >= 1
        assert all(isinstance(r, SearchResult) for r in results)
        assert results[0].source == "amazon_deals"

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_search_empty(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(200, json=CRAWL4AI_EMPTY_RESPONSE)
        )
        client = AmazonDealsClient()
        results = await client.asearch("nothing")
        assert results == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_http_error_raises(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            return_value=httpx.Response(502)
        )
        client = AmazonDealsClient()
        with pytest.raises(httpx.HTTPStatusError):
            await client.asearch("test")

    @respx.mock
    @pytest.mark.asyncio
    async def test_async_timeout_raises(self):
        respx.post(f"{CRAWL4AI_URL}/crawl").mock(
            side_effect=httpx.ReadTimeout("timed out")
        )
        client = AmazonDealsClient()
        with pytest.raises(httpx.TimeoutException):
            await client.asearch("test")


class TestAmazonDealsClientConfig:
    def test_default_crawl4ai_url(self):
        client = AmazonDealsClient()
        # Should use config's crawl4ai_url (from env or default)
        assert client._crawl4ai_url is not None
        assert "11235" in client._crawl4ai_url or "localhost" in client._crawl4ai_url

    def test_default_timeout(self):
        client = AmazonDealsClient()
        # Falls back to 30 if amazon_deals_timeout not on config
        assert client._timeout == 30

    def test_target_url_with_query(self):
        client = AmazonDealsClient()
        url = client._target_url("gaming laptop")
        assert "amazon.com/s" in url
        assert "gaming+laptop" in url or "gaming%20laptop" in url
        assert "price-asc-rank" in url

    def test_target_url_empty_query(self):
        client = AmazonDealsClient()
        url = client._target_url("")
        assert url == "https://www.amazon.com/gp/goldbox"

    def test_target_url_whitespace_query(self):
        client = AmazonDealsClient()
        url = client._target_url("   ")
        assert url == "https://www.amazon.com/gp/goldbox"
