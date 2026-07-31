from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.whodat import WhoDatClient
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_RESPONSE = {
    "domain_name": "example.com",
    "registrar": "Example Registrar Inc.",
    "whois_server": "whois.example-registrar.com",
    "name_servers": ["ns1.example.com", "ns2.example.com", "ns3.example.com", "ns4.example.com"],
    "creation_date": "1995-08-14T00:00:00Z",
    "updated_date": "2024-08-14T07:01:44Z",
    "expiry_date": "2025-08-13T00:00:00Z",
    "status": ["clientDeleteProhibited", "clientTransferProhibited", "clientUpdateProhibited"],
    "dnssec": "signedDelegation",
    "registrant": {
        "name": "REDACTED FOR PRIVACY",
        "organization": "Example Corp",
        "country": "US",
    },
}


@respx.mock
def test_whodat_search():
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = WhoDatClient()
    results = client.search("example.com")
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "example.com"
    assert results[0].source == "whodat"
    assert results[0].url == "https://who.is/whois/example.com"
    assert "Example Registrar Inc." in results[0].snippet
    assert "1995-08-14" in results[0].snippet
    assert "ns1.example.com" in results[0].snippet


@respx.mock
def test_whodat_search_custom_config():
    respx.get("http://localhost:9999/test.org").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(whodat_url="http://localhost:9999")
    client = WhoDatClient(config=config)
    results = client.search("test.org")
    assert len(results) == 1
    assert results[0].source == "whodat"


@respx.mock
def test_whodat_search_error():
    respx.get("http://localhost:8083/bad.com").mock(
        return_value=httpx.Response(500)
    )
    client = WhoDatClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("bad.com")


@respx.mock
def test_whodat_search_empty_fields():
    empty_response = {
        "domain_name": "empty.com",
        "registrar": "",
        "name_servers": [],
        "creation_date": "",
        "expiry_date": "",
        "status": [],
    }
    respx.get("http://localhost:8083/empty.com").mock(
        return_value=httpx.Response(200, json=empty_response)
    )
    client = WhoDatClient()
    results = client.search("empty.com")
    assert len(results) == 1
    assert "N/A" in results[0].snippet


@respx.mock
@pytest.mark.asyncio
async def test_whodat_async_search():
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = WhoDatClient()
    results = await client.asearch("example.com")
    assert len(results) == 1
    assert results[0].source == "whodat"
    assert results[0].title == "example.com"


@respx.mock
def test_whodat_max_results_ignored():
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = WhoDatClient()
    results = client.search("example.com", max_results=10)
    assert len(results) == 1


@respx.mock
def test_whodat_snippet_format():
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = WhoDatClient()
    results = client.search("example.com")
    snippet = results[0].snippet
    assert snippet.startswith("Registrar:")
    assert "Created:" in snippet
    assert "Expires:" in snippet
    assert "Name Servers:" in snippet
    assert "Status:" in snippet
    # Only first 3 name servers
    assert "ns4.example.com" not in snippet
