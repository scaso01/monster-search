from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.deps import DepsClient
from monster_search.config import Config
from monster_search.models import SearchResult

# --- Package info mock data (GET /v3/systems/{system}/packages/{name}) ---

PACKAGE_RESPONSE = {
    "packageKey": {"system": "PYPI", "name": "jinja2"},
    "versions": [
        {
            "versionKey": {"system": "PYPI", "name": "jinja2", "version": "3.1.2"},
            "isDefault": False,
            "publishedAt": "2022-04-28T00:00:00Z",
        },
        {
            "versionKey": {"system": "PYPI", "name": "jinja2", "version": "3.1.3"},
            "isDefault": True,
            "publishedAt": "2024-01-10T00:00:00Z",
        },
        {
            "versionKey": {"system": "PYPI", "name": "jinja2", "version": "3.1.4"},
            "isDefault": False,
            "publishedAt": "2024-05-05T00:00:00Z",
        },
    ],
}

# --- Version detail mock data (GET /v3/systems/{system}/packages/{name}/versions/{version}) ---

VERSION_RESPONSE = {
    "versionKey": {"system": "PYPI", "name": "jinja2", "version": "3.1.3"},
    "isDefault": True,
    "licenses": ["BSD-3-Clause"],
    "advisoryKeys": [
        {"id": "PYSEC-2024-001"},
    ],
    "links": [
        {"label": "SOURCE_REPO", "url": "https://github.com/pallets/jinja"},
        {"label": "HOMEPAGE", "url": "https://jinja.palletsprojects.com/"},
    ],
    "publishedAt": "2024-01-10T00:00:00Z",
}

# --- NPM mock data ---

NPM_PACKAGE_RESPONSE = {
    "packageKey": {"system": "NPM", "name": "express"},
    "versions": [
        {
            "versionKey": {"system": "NPM", "name": "express", "version": "4.18.2"},
            "isDefault": True,
            "publishedAt": "2023-10-01T00:00:00Z",
        },
    ],
}

NPM_VERSION_RESPONSE = {
    "versionKey": {"system": "NPM", "name": "express", "version": "4.18.2"},
    "isDefault": True,
    "licenses": ["MIT"],
    "advisoryKeys": [],
    "links": [
        {"label": "SOURCE_REPO", "url": "https://github.com/expressjs/express"},
    ],
    "publishedAt": "2023-10-01T00:00:00Z",
}


# === Basic Search Tests ===


@respx.mock
def test_deps_search_pypi():
    """Bare package name defaults to PYPI."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(200, json=PACKAGE_RESPONSE)
    )
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2/versions/3.1.3").mock(
        return_value=httpx.Response(200, json=VERSION_RESPONSE)
    )
    client = DepsClient()
    results = client.search("jinja2")
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "deps"
    assert results[0].title == "jinja2@3.1.3 (PYPI)"
    assert results[0].url == "https://github.com/pallets/jinja"
    assert "BSD-3-Clause" in results[0].snippet
    assert "1 advisory" in results[0].snippet
    assert "3 version" in results[0].snippet
    assert results[0].published == "2024-01-10T00:00:00Z"


@respx.mock
def test_deps_search_npm():
    """npm:express routes to NPM system."""
    respx.get("https://api.deps.dev/v3/systems/NPM/packages/express").mock(
        return_value=httpx.Response(200, json=NPM_PACKAGE_RESPONSE)
    )
    respx.get("https://api.deps.dev/v3/systems/NPM/packages/express/versions/4.18.2").mock(
        return_value=httpx.Response(200, json=NPM_VERSION_RESPONSE)
    )
    client = DepsClient()
    results = client.search("npm:express")
    assert len(results) == 1
    assert results[0].title == "express@4.18.2 (NPM)"
    assert results[0].url == "https://github.com/expressjs/express"
    assert "MIT" in results[0].snippet
    assert "0 advisory" in results[0].snippet


@respx.mock
def test_deps_no_source_repo_link():
    """Falls back to deps.dev URL when no SOURCE_REPO link."""
    version_no_repo = {**VERSION_RESPONSE, "links": [{"label": "HOMEPAGE", "url": "https://example.com"}]}
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(200, json=PACKAGE_RESPONSE)
    )
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2/versions/3.1.3").mock(
        return_value=httpx.Response(200, json=version_no_repo)
    )
    client = DepsClient()
    results = client.search("jinja2")
    assert results[0].url == "https://deps.dev/s/pypi/p/jinja2"


@respx.mock
def test_deps_no_default_version():
    """Falls back to last version when no isDefault=True."""
    pkg_no_default = {
        "packageKey": {"system": "PYPI", "name": "mylib"},
        "versions": [
            {
                "versionKey": {"system": "PYPI", "name": "mylib", "version": "1.0.0"},
                "isDefault": False,
            },
            {
                "versionKey": {"system": "PYPI", "name": "mylib", "version": "2.0.0"},
                "isDefault": False,
            },
        ],
    }
    ver_resp = {**VERSION_RESPONSE, "licenses": ["MIT"], "advisoryKeys": [], "links": []}
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/mylib").mock(
        return_value=httpx.Response(200, json=pkg_no_default)
    )
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/mylib/versions/2.0.0").mock(
        return_value=httpx.Response(200, json=ver_resp)
    )
    client = DepsClient()
    results = client.search("mylib")
    assert len(results) == 1
    assert "2.0.0" in results[0].title


# === Custom Config ===


@respx.mock
def test_deps_custom_config():
    """Custom timeout from Config is respected."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(200, json=PACKAGE_RESPONSE)
    )
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2/versions/3.1.3").mock(
        return_value=httpx.Response(200, json=VERSION_RESPONSE)
    )
    config = Config(deps_timeout=60)
    client = DepsClient(config=config)
    results = client.search("jinja2")
    assert len(results) == 1


# === Error Handling ===


@respx.mock
def test_deps_package_not_found():
    """HTTP 404 for unknown package propagates."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/nonexistent-pkg-xyz").mock(
        return_value=httpx.Response(404)
    )
    client = DepsClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("nonexistent-pkg-xyz")


@respx.mock
def test_deps_server_error():
    """HTTP 500 propagates as HTTPStatusError."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(500)
    )
    client = DepsClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("jinja2")


# === Ecosystem Parsing ===


def test_parse_ecosystem():
    """_parse_ecosystem parses ecosystem:package format."""
    client = DepsClient()
    assert client._parse_ecosystem("npm:express") == ("NPM", "express")
    assert client._parse_ecosystem("pypi:jinja2") == ("PYPI", "jinja2")
    assert client._parse_ecosystem("cargo:serde") == ("CARGO", "serde")
    assert client._parse_ecosystem("go:github.com/gin-gonic/gin") == ("GO", "github.com/gin-gonic/gin")
    assert client._parse_ecosystem("maven:org.apache.commons:commons-lang3") == ("MAVEN", "org.apache.commons:commons-lang3")
    assert client._parse_ecosystem("jinja2") == ("PYPI", "jinja2")  # default


# === Async Tests ===


@respx.mock
@pytest.mark.asyncio
async def test_deps_async_search():
    """Async search works."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(200, json=PACKAGE_RESPONSE)
    )
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2/versions/3.1.3").mock(
        return_value=httpx.Response(200, json=VERSION_RESPONSE)
    )
    client = DepsClient()
    results = await client.asearch("jinja2")
    assert len(results) == 1
    assert results[0].source == "deps"
    assert results[0].title == "jinja2@3.1.3 (PYPI)"


@respx.mock
@pytest.mark.asyncio
async def test_deps_async_error():
    """Async HTTP error propagates."""
    respx.get("https://api.deps.dev/v3/systems/PYPI/packages/jinja2").mock(
        return_value=httpx.Response(500)
    )
    client = DepsClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.asearch("jinja2")
