"""Tests for PriceGhost price tracker client."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from monster_search.clients.priceghost import PriceGhostClient
from monster_search.models import SearchResult

# -- Fixtures / mock data ---------------------------------------------------

LOGIN_RESPONSE = {"token": "eyJhbGciOiJIUzI1NiJ9.test-jwt-token.signature"}

MOCK_PRODUCTS = [
    {
        "id": 1,
        "url": "https://www.amazon.com/dp/B0CX23V2ZK",
        "name": "Samsung 990 EVO Plus 2TB NVMe SSD",
        "image_url": "https://m.media-amazon.com/images/I/ssd.jpg",
        "current_price": 149.99,
        "stock_status": "in_stock",
        "refresh_interval": 3600,
        "last_checked": "2026-04-12T10:00:00Z",
        "stats": {
            "min_price": 129.99,
            "max_price": 179.99,
            "avg_price": 155.00,
        },
    },
    {
        "id": 2,
        "url": "https://www.bestbuy.com/site/nvidia-geforce-rtx-4090/6521430.p",
        "name": "NVIDIA GeForce RTX 4090 Founders Edition 24GB",
        "image_url": "https://pisces.bbystatic.com/image/rtx4090.jpg",
        "current_price": 1599.99,
        "stock_status": "out_of_stock",
        "refresh_interval": 1800,
        "last_checked": "2026-04-12T10:15:00Z",
        "stats": {
            "min_price": 1499.99,
            "max_price": 1999.99,
            "avg_price": 1699.99,
        },
    },
    {
        "id": 3,
        "url": "https://www.walmart.com/ip/apple-airpods-pro-2/12345",
        "name": "Apple AirPods Pro 2nd Generation with USB-C",
        "image_url": "https://i5.walmartimages.com/airpods.jpg",
        "current_price": 189.00,
        "stock_status": "in_stock",
        "refresh_interval": 7200,
        "last_checked": "2026-04-12T09:30:00Z",
        "stats": None,
    },
    {
        "id": 4,
        "url": "https://www.newegg.com/samsung-ssd/p/N82E16820147",
        "name": "Samsung 990 Pro 4TB NVMe M.2 SSD",
        "image_url": "https://c1.neweggimages.com/990pro.jpg",
        "current_price": 289.99,
        "stock_status": "in_stock",
        "refresh_interval": 3600,
        "last_checked": "2026-04-12T10:05:00Z",
        "stats": {
            "min_price": 269.99,
            "max_price": 349.99,
            "avg_price": 309.99,
        },
    },
]


def _make_config(**overrides) -> SimpleNamespace:
    """Create a fake config with PriceGhost credentials set.

    Uses SimpleNamespace because Config is a frozen slotted dataclass that
    doesn't have priceghost_* fields.  The client reads them via getattr()
    with defaults, so any object carrying the right attributes works.
    """
    defaults = {
        "priceghost_url": "http://localhost:3100",
        "priceghost_timeout": 15,
        "priceghost_email": "test@example.com",
        "priceghost_password": "testpass123",
        "max_results": 5,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# -- Tests -------------------------------------------------------------------


@respx.mock
def test_priceghost_search_basic():
    """Search returns matching products with price and stock info."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("Samsung SSD")

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.source == "priceghost" for r in results)

    # First result: Samsung 990 EVO Plus
    r0 = results[0]
    assert "Samsung 990 EVO Plus" in r0.title
    assert r0.price == "$149.99"
    assert r0.in_stock is True
    assert "In Stock" in r0.snippet
    assert "$129.99" in r0.snippet  # min price from stats

    # Second result: Samsung 990 Pro
    r1 = results[1]
    assert "Samsung 990 Pro" in r1.title
    assert r1.price == "$289.99"
    assert r1.in_stock is True


@respx.mock
def test_priceghost_search_out_of_stock():
    """Out-of-stock products have in_stock=False."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("RTX 4090")

    assert len(results) == 1
    r = results[0]
    assert "RTX 4090" in r.title
    assert r.price == "$1599.99"
    assert r.in_stock is False
    assert "Out of Stock" in r.snippet


@respx.mock
def test_priceghost_search_no_match():
    """No results when query doesn't match any tracked product."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("PlayStation 5")

    assert results == []


@respx.mock
def test_priceghost_search_empty_products():
    """Empty product list returns empty results."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("anything")

    assert results == []


@respx.mock
def test_priceghost_search_max_results():
    """max_results limits the number of returned results."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    # All 4 products contain some common words, but "Samsung SSD" matches 2
    # Use a broad query that would match multiple items
    results = client.search("Samsung", max_results=1)

    assert len(results) == 1


@respx.mock
def test_priceghost_connection_error():
    """Connection errors return empty list, don't crash."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("SSD")

    assert results == []


@respx.mock
def test_priceghost_timeout():
    """Timeout returns empty list, don't crash."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        side_effect=httpx.ReadTimeout("Read timed out")
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("SSD")

    assert results == []


@respx.mock
def test_priceghost_login_failure():
    """401 on login returns empty list."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(401, json={"error": "Invalid credentials"})
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("SSD")

    assert results == []


@respx.mock
def test_priceghost_no_credentials():
    """Missing credentials returns empty list without making any requests."""
    client = PriceGhostClient(config=_make_config(priceghost_email="", priceghost_password=""))
    results = client.search("SSD")

    assert results == []


@respx.mock
def test_priceghost_token_cached():
    """Token is cached across multiple searches (only one login call)."""
    login_route = respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    client.search("Samsung")
    client.search("NVIDIA")

    assert login_route.call_count == 1


@respx.mock
def test_priceghost_token_cleared_on_401():
    """Expired token is cleared on 401, allowing re-login on next call."""
    login_route = respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    products_route = respx.get("http://localhost:3100/api/products")

    # First call: success
    products_route.mock(return_value=httpx.Response(200, json=MOCK_PRODUCTS))
    client = PriceGhostClient(config=_make_config())
    results = client.search("Samsung")
    assert len(results) == 2
    assert login_route.call_count == 1

    # Simulate token expiry
    products_route.mock(return_value=httpx.Response(401, json={"error": "Unauthorized"}))
    results = client.search("Samsung")
    assert results == []
    assert client._token is None  # Token cleared

    # Next call should re-login
    products_route.mock(return_value=httpx.Response(200, json=MOCK_PRODUCTS))
    results = client.search("Samsung")
    assert len(results) == 2
    assert login_route.call_count == 2


@respx.mock
def test_priceghost_product_no_price():
    """Products with no current_price are handled gracefully."""
    products_no_price = [
        {
            "id": 10,
            "url": "https://www.amazon.com/dp/B0NOPR1CE",
            "name": "Mystery Widget",
            "current_price": None,
            "stock_status": "unknown",
        },
    ]
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=products_no_price)
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("Mystery Widget")

    assert len(results) == 1
    assert results[0].price is None
    assert results[0].in_stock is None
    assert "Stock: Unknown" in results[0].snippet


@respx.mock
def test_priceghost_custom_url():
    """Client uses custom base URL from config."""
    respx.post("http://localhost:9999/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:9999/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    config = _make_config(priceghost_url="http://localhost:9999")
    client = PriceGhostClient(config=config)
    results = client.search("Samsung")

    assert len(results) == 2
    assert all(r.source == "priceghost" for r in results)


@respx.mock
@pytest.mark.asyncio
async def test_priceghost_async_search():
    """Async search works the same as sync."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    results = await client.asearch("RTX 4090")

    assert len(results) == 1
    assert results[0].source == "priceghost"
    assert results[0].price == "$1599.99"
    assert results[0].in_stock is False


@respx.mock
@pytest.mark.asyncio
async def test_priceghost_async_connection_error():
    """Async connection errors return empty list."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    client = PriceGhostClient(config=_make_config())
    results = await client.asearch("SSD")

    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_priceghost_async_no_credentials():
    """Async with missing credentials returns empty list."""
    client = PriceGhostClient(config=_make_config(priceghost_email="", priceghost_password=""))
    results = await client.asearch("SSD")

    assert results == []


@respx.mock
def test_priceghost_products_as_dict():
    """Handle response where products is wrapped in a dict."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json={"products": MOCK_PRODUCTS})
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("AirPods")

    assert len(results) == 1
    assert "AirPods Pro" in results[0].title


@respx.mock
def test_priceghost_snippet_format():
    """Snippet contains price, stock, and price range information."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(200, json=MOCK_PRODUCTS)
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("Samsung 990 EVO")

    assert len(results) == 1
    snippet = results[0].snippet
    assert "Price: $149.99" in snippet
    assert "In Stock" in snippet
    assert "Range: $129.99 - $179.99" in snippet


@respx.mock
def test_priceghost_server_error():
    """500 from products endpoint returns empty list."""
    respx.post("http://localhost:3100/api/auth/login").mock(
        return_value=httpx.Response(200, json=LOGIN_RESPONSE)
    )
    respx.get("http://localhost:3100/api/products").mock(
        return_value=httpx.Response(500, json={"error": "Internal Server Error"})
    )

    client = PriceGhostClient(config=_make_config())
    results = client.search("SSD")

    assert results == []
