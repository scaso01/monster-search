"""Tests for CheapShark PC game price comparison client."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.cheapshark import CheapSharkClient
from monster_search.models import SearchResult


CHEAPSHARK_RESPONSE = [
    {
        "title": "Elden Ring",
        "dealID": "abc123def456",
        "storeID": "1",
        "salePrice": "29.99",
        "normalPrice": "59.99",
        "savings": "50.008335",
        "steamAppID": "1245620",
        "thumb": "https://cdn.cloudflare.steamstatic.com/steam/apps/1245620/capsule_sm_120.jpg",
    },
    {
        "title": "Hades",
        "dealID": "ghi789jkl012",
        "storeID": "7",
        "salePrice": "9.99",
        "normalPrice": "24.99",
        "savings": "60.024010",
        "steamAppID": "1145360",
        "thumb": "https://cdn.cloudflare.steamstatic.com/steam/apps/1145360/capsule_sm_120.jpg",
    },
    {
        "title": "Cyberpunk 2077",
        "dealID": "mno345pqr678",
        "storeID": "3",
        "salePrice": "14.99",
        "normalPrice": "29.99",
        "savings": "50.016672",
        "steamAppID": "1091500",
        "thumb": "https://cdn.cloudflare.steamstatic.com/steam/apps/1091500/capsule_sm_120.jpg",
    },
]

CHEAPSHARK_EMPTY: list[dict] = []


@respx.mock
def test_cheapshark_search():
    """Basic search returns SearchResults with correct fields and prices."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_RESPONSE)
    )
    client = CheapSharkClient()
    results = client.search("elden ring", max_results=5)

    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)

    # First result
    assert results[0].title == "Elden Ring"
    assert results[0].source == "cheapshark"
    assert results[0].url == "https://www.cheapshark.com/redirect?dealID=abc123def456"
    assert results[0].price == "$29.99"
    assert results[0].in_stock is True
    assert "Sale: $29.99" in results[0].snippet
    assert "was $59.99" in results[0].snippet
    assert "save 50%" in results[0].snippet
    assert "Store ID: 1" in results[0].snippet


@respx.mock
def test_cheapshark_empty():
    """Empty response returns empty list."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_EMPTY)
    )
    client = CheapSharkClient()
    results = client.search("xyznonexistent123")
    assert results == []


@respx.mock
def test_cheapshark_error():
    """HTTP 500 raises HTTPStatusError."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(500)
    )
    client = CheapSharkClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_cheapshark_timeout():
    """ReadTimeout raises TimeoutException."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = CheapSharkClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_cheapshark_sends_correct_params():
    """Verify title, onSale, and pageSize params in request URL."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_EMPTY)
    )
    client = CheapSharkClient()
    client.search("hades", max_results=10)

    request = respx.calls[0].request
    url_str = str(request.url)
    assert "title=hades" in url_str
    assert "onSale=1" in url_str
    assert "pageSize=10" in url_str


@respx.mock
def test_cheapshark_price_formatting():
    """Prices are formatted with dollar sign prefix."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_RESPONSE)
    )
    client = CheapSharkClient()
    results = client.search("cyberpunk", max_results=5)

    # Check all results have dollar-prefixed prices
    for r in results:
        assert r.price is not None
        assert r.price.startswith("$")

    # Second result: Hades at $9.99
    assert results[1].price == "$9.99"
    assert results[1].title == "Hades"

    # Third result: Cyberpunk at $14.99
    assert results[2].price == "$14.99"


@respx.mock
def test_cheapshark_savings_formatting():
    """Savings percentage is rounded to integer in snippet."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_RESPONSE)
    )
    client = CheapSharkClient()
    results = client.search("deals", max_results=5)

    # Hades: 60.024010% -> "save 60%"
    assert "save 60%" in results[1].snippet


@respx.mock
@pytest.mark.asyncio
async def test_cheapshark_async_search():
    """Async search works."""
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=CHEAPSHARK_RESPONSE)
    )
    client = CheapSharkClient()
    results = await client.asearch("elden ring", max_results=5)

    assert len(results) == 3
    assert results[0].source == "cheapshark"
    assert results[0].title == "Elden Ring"
    assert results[0].price == "$29.99"
    assert results[0].in_stock is True
