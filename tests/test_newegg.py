"""Tests for Newegg product search client (via Crawl4AI scraping)."""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx

from monster_search.clients._pool import close_all
from monster_search.clients.newegg import (
    NeweggClient,
    _build_search_url,
    _clean_url,
    _extract_markdown,
    _extract_price_near,
    _is_product_url,
    _parse_products,
)
from monster_search.models import SearchResult


@pytest.fixture(autouse=True)
def _clean_pool():
    """Clear pooled HTTP clients before each test to avoid cross-test interference."""
    close_all()
    yield
    close_all()


# ---------------------------------------------------------------------------
# Realistic Crawl4AI response fixtures
# ---------------------------------------------------------------------------

# Simulates markdown output from Crawl4AI after rendering a Newegg search page.
NEWEGG_MARKDOWN = """
# Search Results for "RTX 4090"

## Featured Products

ASUS TUF Gaming GeForce RTX 4090 OC Edition 24GB GDDR6X
[ASUS TUF Gaming GeForce RTX 4090 OC Edition 24GB GDDR6X](https://www.newegg.com/asus-geforce-rtx-4090-tuf-rtx4090-o24g-gaming/p/N82E16814126598)

$1,799.99  $1,899.99 | **Save: 5%**

Free Shipping | Arrives by Fri, Apr 18

---

MSI GeForce RTX 4090 SUPRIM LIQUID X 24G Graphics Card
[MSI GeForce RTX 4090 SUPRIM LIQUID X 24G](https://www.newegg.com/msi-geforce-rtx-4090-rtx-4090-suprim-liquid-x-24g/p/N82E16814137782)

$2,149.99

Free Shipping

---

GIGABYTE GeForce RTX 4090 WINDFORCE V2 24G Graphics Card
[GIGABYTE GeForce RTX 4090 WINDFORCE V2 24G](https://www.newegg.com/gigabyte-geforce-rtx-4090-gv-n4090wf3v2-24gd/p/N82E16814932618)

$1,649.99  $1,799.99 | **Save: 8%**

Free Shipping | Arrives by Thu, Apr 17

---

Navigation Links:
[Home](https://www.newegg.com/)
[Computer Hardware](https://www.newegg.com/Computer-Hardware/Store/ID-7)
[Video Cards](https://www.newegg.com/p/pl?d=video+cards)
[Add to Cart](https://www.newegg.com/cart)
"""

NEWEGG_CRAWL4AI_RESPONSE = {
    "results": [
        {
            "markdown": NEWEGG_MARKDOWN,
            "metadata": {"title": "RTX 4090 - Newegg.com"},
            "html": "<html>...</html>",
        }
    ]
}

NEWEGG_CRAWL4AI_DICT_MARKDOWN = {
    "results": [
        {
            "markdown": {
                "raw_markdown": NEWEGG_MARKDOWN,
                "fit_markdown": "",
            },
            "metadata": {"title": "RTX 4090 - Newegg.com"},
        }
    ]
}

NEWEGG_EMPTY_RESPONSE = {
    "results": [
        {
            "markdown": "# No Results Found\n\nSorry, we didn't find any matches for your search.\n\n[Home](https://www.newegg.com/)\n",
            "metadata": {"title": "Search - Newegg.com"},
        }
    ]
}

NEWEGG_NO_RESULTS_RESPONSE: dict = {"results": []}


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestBuildSearchUrl:
    def test_basic_query(self):
        url = _build_search_url("RTX 4090")
        assert "d=RTX+4090" in url
        assert "Order=1" in url
        assert url.startswith("https://www.newegg.com/p/pl?")

    def test_special_characters(self):
        url = _build_search_url("cpu & motherboard combo")
        assert "d=cpu+%26+motherboard+combo" in url


class TestIsProductUrl:
    def test_product_page(self):
        assert _is_product_url("https://www.newegg.com/asus/p/N82E16814126598") is True

    def test_search_listing_page(self):
        assert _is_product_url("https://www.newegg.com/p/pl?d=video+cards") is False

    def test_item_id_pattern(self):
        assert _is_product_url("https://www.newegg.com/N82E16814126598") is True

    def test_home_page(self):
        assert _is_product_url("https://www.newegg.com/") is False


class TestExtractMarkdown:
    def test_string_markdown(self):
        md = _extract_markdown(NEWEGG_CRAWL4AI_RESPONSE)
        assert "RTX 4090" in md

    def test_dict_markdown(self):
        md = _extract_markdown(NEWEGG_CRAWL4AI_DICT_MARKDOWN)
        assert "RTX 4090" in md

    def test_empty_results(self):
        assert _extract_markdown({"results": []}) == ""

    def test_missing_results_key(self):
        assert _extract_markdown({}) == ""


class TestParseProducts:
    def test_extracts_products(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        assert len(results) >= 2
        assert all(isinstance(r, SearchResult) for r in results)

    def test_product_titles(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        titles = [r.title for r in results]
        assert any("ASUS" in t for t in titles)
        assert any("MSI" in t for t in titles)

    def test_product_urls(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        for r in results:
            assert r.url.startswith("https://www.newegg.com/")
            assert "/p/" in r.url or re.search(r"/[\dA-Z]{10,}", r.url)

    def test_price_extraction(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        prices = [r.price for r in results if r.price]
        assert len(prices) >= 2
        assert "$1,799.99" in prices or "$1,649.99" in prices

    def test_source_is_newegg(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        for r in results:
            assert r.source == "newegg"

    def test_skips_navigation_links(self):
        results = _parse_products(NEWEGG_MARKDOWN, 10)
        urls = [r.url for r in results]
        assert "https://www.newegg.com/" not in urls
        assert "https://www.newegg.com/cart" not in urls

    def test_max_results_limit(self):
        results = _parse_products(NEWEGG_MARKDOWN, 1)
        assert len(results) == 1

    def test_no_products_returns_empty(self):
        no_products_md = "# Welcome to Newegg\n\n[Home](https://www.newegg.com/)\n"
        results = _parse_products(no_products_md, 10)
        assert results == []

    def test_deduplicates_urls(self):
        dupe_md = (
            "[ASUS RTX 4090 Gaming Card](https://www.newegg.com/asus/p/N82E16814126598)\n"
            "$1,799.99\n"
            "[ASUS RTX 4090 Gaming Card](https://www.newegg.com/asus/p/N82E16814126598)\n"
            "$1,799.99\n"
        )
        results = _parse_products(dupe_md, 10)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Integration-style tests (mocked HTTP)
# ---------------------------------------------------------------------------


@respx.mock
def test_newegg_search_sync():
    """Sync search returns parsed SearchResults with correct fields."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("RTX 4090", max_results=5)

    assert len(results) >= 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "newegg"
    assert results[0].url.startswith("https://www.newegg.com/")
    # Verify price is present on at least one result
    prices = [r.price for r in results if r.price]
    assert len(prices) >= 1


@respx.mock
def test_newegg_search_sends_correct_url():
    """Verify the correct Newegg URL is sent to Crawl4AI."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    client.search("RTX 4090", max_results=5)

    request = respx.calls[0].request
    body = json.loads(request.content)
    assert "urls" in body
    assert "newegg.com" in body["urls"][0]
    assert "RTX+4090" in body["urls"][0]
    assert "Order=1" in body["urls"][0]


@respx.mock
def test_newegg_empty_results():
    """No-results response returns empty list gracefully."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_EMPTY_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("xyznonexistent123widget")
    assert results == []


@respx.mock
def test_newegg_no_results_key():
    """Empty results array from Crawl4AI returns empty list."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_NO_RESULTS_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("test")
    assert results == []


@respx.mock
def test_newegg_http_error():
    """HTTP 500 from Crawl4AI raises HTTPStatusError."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(500)
    )
    client = NeweggClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_newegg_timeout():
    """ReadTimeout raises TimeoutException."""
    respx.post("http://localhost:11235/crawl").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = NeweggClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_newegg_dict_markdown_format():
    """Handles Crawl4AI response where markdown is a dict with raw_markdown."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_DICT_MARKDOWN)
    )
    client = NeweggClient()
    results = client.search("RTX 4090", max_results=5)
    assert len(results) >= 2


@respx.mock
@pytest.mark.asyncio
async def test_newegg_async_search():
    """Async search returns correct results."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    results = await client.asearch("RTX 4090", max_results=5)

    assert len(results) >= 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "newegg"
    prices = [r.price for r in results if r.price]
    assert len(prices) >= 1


@respx.mock
@pytest.mark.asyncio
async def test_newegg_async_error():
    """Async search raises on HTTP error."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(502)
    )
    client = NeweggClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.asearch("test")


@respx.mock
def test_newegg_max_results_respected():
    """max_results limits the number of returned products."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("RTX 4090", max_results=1)
    assert len(results) == 1


@respx.mock
def test_newegg_price_format():
    """Prices include dollar sign and proper formatting."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("RTX 4090", max_results=5)

    for r in results:
        if r.price:
            assert r.price.startswith("$")
            # Should be a valid price pattern
            assert re.match(r"^\$[\d,]+\.?\d*$", r.price)


# ---------------------------------------------------------------------------
# Realistic Crawl4AI markdown (matches actual Newegg page structure)
# ---------------------------------------------------------------------------

# This mirrors the actual markdown that Crawl4AI produces when scraping a
# Newegg search results page.  Product titles are in image alt text, not
# in plain markdown links.  The "More options from $X - $Y" links are UI
# elements that should NOT be used as product titles.
NEWEGG_REAL_MARKDOWN = """\
Add to cart
Compare
[![GIGABYTE WindForce GeForce RTX 5070 12GB GDDR7 PCI Express 5.0 ATX Graphics Card GV-N5070WF3OC-12GD](https://c1.neweggimages.com/productimage/nb300/14-932-777-02.jpg)Quick View](https://www.newegg.com/gigabyte-windforce-gv-n5070wf3oc-12gd-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814932777?Item=N82E16814932777)
[![GIGABYTE](https://c1.neweggimages.com/brandimage/Brand1314.gif)](https://www.newegg.com/GIGABYTE/BrandStore/ID-1314)[(129)](https://www.newegg.com/gigabyte-windforce-gv-n5070wf3oc-12gd-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814932777?Item=N82E16814932777#IsFeedbackTab "Rating + 4")
[](https://www.newegg.com/gigabyte-windforce-gv-n5070wf3oc-12gd-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814932777?Item=N82E16814932777 "View Details")
  * **Model #:** GV-N5070WF3OC-12GD


  * $649.99
  * **635**.99 –
  * [More options from $549.99 - $1,010.90](https://www.newegg.com/gigabyte-windforce-gv-n5070wf3oc-12gd-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814932777?Item=N82E16814932777#moreBuyOptions)


Free Shipping  from United States
Add to cart
Compare
[![MSI SHADOW GeForce RTX 5070 12GB GDDR7 PCI Express 5.0 ATX Graphics Card RTX 5070 12G SHADOW 2X OC](https://c1.neweggimages.com/productimage/nb300/14-137-944-11.jpg)Quick View](https://www.newegg.com/msi-rtx-5070-12g-shadow-2x-oc-geforce-rtx-5070-12gb-graphics-card-double-fans/p/N82E16814137944?Item=N82E16814137944)
[![MSI](https://c1.neweggimages.com/brandimage/Brand1312.gif)](https://www.newegg.com/MSI/BrandStore/ID-1312)[(188)](https://www.newegg.com/msi-rtx-5070-12g-shadow-2x-oc-geforce-rtx-5070-12gb-graphics-card-double-fans/p/N82E16814137944?Item=N82E16814137944#IsFeedbackTab "Rating + 4.7")
[](https://www.newegg.com/msi-rtx-5070-12g-shadow-2x-oc-geforce-rtx-5070-12gb-graphics-card-double-fans/p/N82E16814137944?Item=N82E16814137944 "View Details")
  * **Model #:** RTX 5070 12G SHADOW 2X OC


  * **649**.99 –
  * [More options from $635.99 - $970.90](https://www.newegg.com/msi-rtx-5070-12g-shadow-2x-oc-geforce-rtx-5070-12gb-graphics-card-double-fans/p/N82E16814137944?Item=N82E16814137944#moreBuyOptions)


Free Shipping  from United States
Add to cart
Compare
[![ASUS PRIME GeForce RTX 5070 12GB 192-Bit GDDR7 PCI Express 5.0 Graphics Card PRIME-RTX5070-12G](https://c1.neweggimages.com/productimage/nb300/14-126-761-01.png)Quick View](https://www.newegg.com/asus-prime-rtx5070-12g-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814126761?Item=N82E16814126761)
[![ASUS](https://c1.neweggimages.com/brandimage/Brand1315.gif)](https://www.newegg.com/ASUS/BrandStore/ID-1315)[(166)](https://www.newegg.com/asus-prime-rtx5070-12g-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814126761?Item=N82E16814126761#IsFeedbackTab "Rating + 4.6")
[](https://www.newegg.com/asus-prime-rtx5070-12g-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814126761?Item=N82E16814126761 "View Details")
  * **Model #:** PRIME-RTX5070-12G


  * **669**.99 –
  * [More options from $625.49 - $994.99](https://www.newegg.com/asus-prime-rtx5070-12g-geforce-rtx-5070-12gb-graphics-card-triple-fans/p/N82E16814126761?Item=N82E16814126761#moreBuyOptions)


Free Shipping  from United States

Navigation Links:
[Home](https://www.newegg.com/)
[Computer Hardware](https://www.newegg.com/Computer-Hardware/Store/ID-7)
"""

NEWEGG_REAL_CRAWL4AI_RESPONSE = {
    "results": [
        {
            "markdown": NEWEGG_REAL_MARKDOWN,
            "metadata": {"title": "RTX 5070 - Newegg.com"},
        }
    ]
}


# ---------------------------------------------------------------------------
# Tests for realistic Crawl4AI markdown (image-alt-text titles)
# ---------------------------------------------------------------------------


class TestParseRealProducts:
    """Tests using markdown that mirrors actual Crawl4AI Newegg output."""

    def test_extracts_real_product_titles(self):
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        titles = [r.title for r in results]
        assert len(results) == 3
        assert any("GIGABYTE WindForce" in t for t in titles)
        assert any("MSI SHADOW" in t for t in titles)
        assert any("ASUS PRIME" in t for t in titles)

    def test_no_more_options_in_titles(self):
        """'More options from $X' must never appear as a product title."""
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        for r in results:
            assert "More options" not in r.title
            assert "more options" not in r.title.lower()

    def test_extracts_bold_prices(self):
        """Prices should come from the **bold**.99 format, not the range."""
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        prices = [r.price for r in results if r.price]
        assert len(prices) >= 2
        # Gigabyte should be $635.99 (from **635**.99)
        assert "$635.99" in prices
        # MSI should be $649.99
        assert "$649.99" in prices
        # ASUS should be $669.99
        assert "$669.99" in prices

    def test_product_urls_cleaned(self):
        """URLs should not contain fragment anchors like #moreBuyOptions."""
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        for r in results:
            assert "#moreBuyOptions" not in r.url
            assert r.url.startswith("https://www.newegg.com/")

    def test_deduplicates_across_passes(self):
        """Products found in pass 1 (image links) should not repeat in pass 2."""
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        urls = [r.url for r in results]
        assert len(urls) == len(set(urls))

    def test_model_number_in_snippet(self):
        """Snippet should contain useful info like model number."""
        results = _parse_products(NEWEGG_REAL_MARKDOWN, 10)
        # At least one result should have a Model # in its snippet
        assert any("Model #" in r.snippet or "GV-N5070" in r.snippet for r in results)


class TestMoreOptionsFiltering:
    """Verify that 'More options from...' links are always filtered."""

    def test_more_options_only_markdown(self):
        """Markdown with only 'More options' links returns empty."""
        md = (
            "[More options from $549.99 - $1,010.90]"
            "(https://www.newegg.com/some-product/p/N82E16814932777#moreBuyOptions)\n"
            "[More options from $649.99 - $767.95]"
            "(https://www.newegg.com/other-product/p/N82E16814500642#moreBuyOptions)\n"
        )
        results = _parse_products(md, 10)
        assert results == []

    def test_mixed_more_options_and_real_titles(self):
        """Real product links should be kept, 'More options' links filtered."""
        md = (
            "[ASUS RTX 4090 Gaming Card 24GB](https://www.newegg.com/asus/p/N82E16814126598)\n"
            "$1,799.99\n"
            "[More options from $1,799 - $2,000](https://www.newegg.com/asus/p/N82E16814126598#moreBuyOptions)\n"
        )
        results = _parse_products(md, 10)
        assert len(results) == 1
        assert "ASUS RTX 4090" in results[0].title


class TestCleanUrl:
    def test_strips_fragment(self):
        url = "https://www.newegg.com/product/p/N82E16814932777?Item=ABC#moreBuyOptions"
        assert _clean_url(url) == "https://www.newegg.com/product/p/N82E16814932777?Item=ABC"

    def test_no_fragment_unchanged(self):
        url = "https://www.newegg.com/product/p/N82E16814932777"
        assert _clean_url(url) == url


class TestExtractPriceNear:
    def test_bold_price_preferred(self):
        md = "  * $699.99\n  * **649**.99 –\n  * Save: $50.00 (7%)"
        assert _extract_price_near(md, 0) == "$649.99"

    def test_plain_price_fallback(self):
        md = "  * $699.99\n  * Some other text"
        assert _extract_price_near(md, 0) == "$699.99"

    def test_no_price_returns_none(self):
        md = "Free Shipping from United States"
        assert _extract_price_near(md, 0) is None

    def test_bold_price_with_comma(self):
        md = "  * **1,799**.99 –"
        assert _extract_price_near(md, 0) == "$1,799.99"


@respx.mock
def test_newegg_real_markdown_integration():
    """Integration test with realistic Crawl4AI markdown structure."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=NEWEGG_REAL_CRAWL4AI_RESPONSE)
    )
    client = NeweggClient()
    results = client.search("RTX 5070", max_results=5)

    assert len(results) == 3
    # Titles should be actual product names, not "More options from..."
    for r in results:
        assert "More options" not in r.title
        assert r.source == "newegg"
    # Should have real product names
    titles = [r.title for r in results]
    assert any("GIGABYTE" in t for t in titles)
    assert any("MSI" in t for t in titles)
    assert any("ASUS" in t for t in titles)
