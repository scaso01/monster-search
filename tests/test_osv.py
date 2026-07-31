from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.osv import OsvClient
from monster_search.config import Config
from monster_search.models import SearchResult

# --- Single vuln lookup mock data (GET /v1/vulns/{id}) ---

SINGLE_VULN = {
    "id": "CVE-2023-12345",
    "summary": "Remote code execution in example-lib",
    "details": "A critical vulnerability in example-lib allows remote attackers to execute arbitrary code via crafted input to the parse() function.",
    "published": "2023-06-15T00:00:00Z",
    "modified": "2023-07-01T00:00:00Z",
    "aliases": ["GHSA-xxxx-yyyy-zzzz"],
    "references": [
        {"type": "ADVISORY", "url": "https://nvd.nist.gov/vuln/detail/CVE-2023-12345"},
        {"type": "WEB", "url": "https://example.com/advisory"},
    ],
    "affected": [
        {
            "package": {"name": "example-lib", "ecosystem": "PyPI"},
            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "2.1.0"}]}],
        }
    ],
    "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}],
}

# --- Package query mock data (POST /v1/query) ---

PACKAGE_VULNS_RESPONSE = {
    "vulns": [
        {
            "id": "PYSEC-2023-001",
            "summary": "SQL injection in orm-lib",
            "details": "An SQL injection vulnerability exists in orm-lib's query builder.",
            "published": "2023-03-01T00:00:00Z",
            "modified": "2023-03-15T00:00:00Z",
            "references": [
                {"type": "WEB", "url": "https://github.com/example/orm-lib/security/advisories/1"},
            ],
            "affected": [
                {"package": {"name": "orm-lib", "ecosystem": "PyPI"}},
            ],
        },
        {
            "id": "PYSEC-2023-002",
            "summary": "Path traversal in orm-lib",
            "details": "A path traversal vulnerability in orm-lib allows reading arbitrary files.",
            "published": "2023-05-10T00:00:00Z",
            "modified": "2023-05-20T00:00:00Z",
            "references": [],
            "affected": [
                {"package": {"name": "orm-lib", "ecosystem": "PyPI"}},
            ],
        },
    ]
}


# === CVE/GHSA ID Lookup Tests ===


@respx.mock
def test_osv_cve_lookup():
    """CVE-* queries route to GET /v1/vulns/{id}."""
    respx.get("https://api.osv.dev/v1/vulns/CVE-2023-12345").mock(
        return_value=httpx.Response(200, json=SINGLE_VULN)
    )
    client = OsvClient()
    results = client.search("CVE-2023-12345")
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "osv"
    assert "CVE-2023-12345" in results[0].title
    assert "Remote code execution" in results[0].title
    assert results[0].url == "https://nvd.nist.gov/vuln/detail/CVE-2023-12345"
    assert results[0].published == "2023-06-15T00:00:00Z"
    assert results[0].category == "PyPI"


@respx.mock
def test_osv_ghsa_lookup():
    """GHSA-* queries route to GET /v1/vulns/{id}."""
    vuln = {**SINGLE_VULN, "id": "GHSA-xxxx-yyyy-zzzz"}
    respx.get("https://api.osv.dev/v1/vulns/GHSA-xxxx-yyyy-zzzz").mock(
        return_value=httpx.Response(200, json=vuln)
    )
    client = OsvClient()
    results = client.search("GHSA-xxxx-yyyy-zzzz")
    assert len(results) == 1
    assert "GHSA-xxxx-yyyy-zzzz" in results[0].title


@respx.mock
def test_osv_vuln_no_references():
    """Vuln with no references falls back to osv.dev URL."""
    vuln = {**SINGLE_VULN, "references": []}
    respx.get("https://api.osv.dev/v1/vulns/CVE-2023-12345").mock(
        return_value=httpx.Response(200, json=vuln)
    )
    client = OsvClient()
    results = client.search("CVE-2023-12345")
    assert results[0].url == "https://osv.dev/vulnerability/CVE-2023-12345"


# === Package Query Tests ===


@respx.mock
def test_osv_package_query():
    """Package name queries route to POST /v1/query."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json=PACKAGE_VULNS_RESPONSE)
    )
    client = OsvClient()
    results = client.search("orm-lib")
    assert len(results) == 2
    assert results[0].source == "osv"
    assert "PYSEC-2023-001" in results[0].title
    assert results[0].category == "PyPI"


@respx.mock
def test_osv_package_with_ecosystem():
    """ecosystem:package format sets correct ecosystem in POST body."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json=PACKAGE_VULNS_RESPONSE)
    )
    client = OsvClient()
    results = client.search("npm:express")
    assert len(results) == 2
    # Verify the POST body
    request = respx.calls[0].request
    import json

    body = json.loads(request.content)
    assert body["package"]["name"] == "express"
    assert body["package"]["ecosystem"] == "npm"


@respx.mock
def test_osv_max_results():
    """max_results slices the vulns array."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json=PACKAGE_VULNS_RESPONSE)
    )
    client = OsvClient()
    results = client.search("orm-lib", max_results=1)
    assert len(results) == 1


@respx.mock
def test_osv_empty_results():
    """Empty vulns array returns empty list."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json={"vulns": []})
    )
    client = OsvClient()
    results = client.search("nonexistent-package")
    assert results == []


@respx.mock
def test_osv_no_vulns_key():
    """Missing vulns key returns empty list."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json={})
    )
    client = OsvClient()
    results = client.search("clean-package")
    assert results == []


# === Error Handling ===


@respx.mock
def test_osv_http_error():
    """HTTP 500 propagates as HTTPStatusError."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(500)
    )
    client = OsvClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_osv_cve_not_found():
    """HTTP 404 for unknown CVE propagates."""
    respx.get("https://api.osv.dev/v1/vulns/CVE-9999-99999").mock(
        return_value=httpx.Response(404)
    )
    client = OsvClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("CVE-9999-99999")


# === Custom Config ===


@respx.mock
def test_osv_custom_config():
    """Custom timeout from Config is respected."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json=PACKAGE_VULNS_RESPONSE)
    )
    config = Config(osv_timeout=60)
    client = OsvClient(config=config)
    results = client.search("orm-lib")
    assert len(results) == 2


# === Async Tests ===


@respx.mock
@pytest.mark.asyncio
async def test_osv_async_package_query():
    """Async package query works."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json=PACKAGE_VULNS_RESPONSE)
    )
    client = OsvClient()
    results = await client.asearch("orm-lib")
    assert len(results) == 2
    assert results[0].source == "osv"


@respx.mock
@pytest.mark.asyncio
async def test_osv_async_cve_lookup():
    """Async CVE lookup works."""
    respx.get("https://api.osv.dev/v1/vulns/CVE-2023-12345").mock(
        return_value=httpx.Response(200, json=SINGLE_VULN)
    )
    client = OsvClient()
    results = await client.asearch("CVE-2023-12345")
    assert len(results) == 1
    assert "CVE-2023-12345" in results[0].title


@respx.mock
@pytest.mark.asyncio
async def test_osv_async_error():
    """Async HTTP error propagates."""
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(500)
    )
    client = OsvClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.asearch("test")


# === Routing Detection ===


def test_is_vuln_id_detection():
    """_is_vuln_id correctly identifies CVE and GHSA prefixes."""
    client = OsvClient()
    assert client._is_vuln_id("CVE-2023-12345") is True
    assert client._is_vuln_id("GHSA-xxxx-yyyy-zzzz") is True
    assert client._is_vuln_id("cve-2023-12345") is True  # case-insensitive
    assert client._is_vuln_id("ghsa-xxxx-yyyy-zzzz") is True
    assert client._is_vuln_id("jinja2") is False
    assert client._is_vuln_id("npm:express") is False


def test_parse_ecosystem():
    """_parse_ecosystem parses ecosystem:package format."""
    client = OsvClient()
    assert client._parse_ecosystem("npm:express") == ("npm", "express")
    assert client._parse_ecosystem("pypi:jinja2") == ("PyPI", "jinja2")
    assert client._parse_ecosystem("cargo:serde") == ("crates.io", "serde")
    assert client._parse_ecosystem("jinja2") == ("PyPI", "jinja2")  # default
