"""Tests for SearchcodeRepoClient (searchcode.com per-repo code search)."""

from __future__ import annotations

import pytest
import respx
import httpx

from monster_search.clients.searchcode_repo import SearchcodeRepoClient
from monster_search.config import Config


_REPO_URL = "https://github.com/encode/httpx"

# Minimal realistic searchcode API response shape
_MOCK_RESPONSE = {
    "results": [
        {
            "repo": "httpx",
            "location": "/httpx/_client.py",
            "url": "https://searchcode.com/file/123456/httpx/_client.py",
            "md5hash": "abc123def456",
            "sha1hash": "",
            "language": "Python",
            "matches": [
                {"line": 42, "line_content": "import httpx"},
                {"line": 87, "line_content": "    client = httpx.Client()"},
            ],
        },
        {
            "repo": "httpx",
            "location": "/tests/test_client.py",
            "url": "https://searchcode.com/file/789012/tests/test_client.py",
            "md5hash": "deadbeef0000",
            "sha1hash": "",
            "language": "Python",
            "matches": [
                {"line": 5, "line_content": "import httpx"},
            ],
        },
    ]
}


@respx.mock
def test_searchcode_repo_basic():
    """Two file results with two matches each → two SearchResult objects per file match."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    config = Config(max_results=10)
    client = SearchcodeRepoClient(config=config)
    results = client.search("import httpx", repository=_REPO_URL)

    assert len(results) == 3  # 2 matches from file 1 + 1 from file 2
    assert all(r.source == "searchcode_repo" for r in results)


@respx.mock
def test_searchcode_repo_max_results():
    """max_results caps the total number of SearchResult items."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    config = Config(max_results=2)
    client = SearchcodeRepoClient(config=config)
    results = client.search("import httpx", repository=_REPO_URL, max_results=2)
    assert len(results) == 2


@respx.mock
def test_searchcode_repo_github_blob_url():
    """Results from a GitHub repo get GitHub blob URLs built from md5hash."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    client = SearchcodeRepoClient()
    results = client.search("import httpx", repository=_REPO_URL, max_results=1)
    assert results[0].url.startswith("https://github.com/encode/httpx/blob/")
    assert "httpx/_client.py" in results[0].url


@respx.mock
def test_searchcode_repo_non_github_url():
    """Non-GitHub repos fall back to searchcode.com URL."""
    mock_resp = {
        "results": [
            {
                "repo": "myrepo",
                "location": "/src/main.py",
                "url": "https://searchcode.com/file/999/src/main.py",
                "md5hash": "",
                "sha1hash": "",
                "language": "Python",
                "matches": [{"line": 1, "line_content": "def main():"}],
            }
        ]
    }
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=mock_resp)
    )
    client = SearchcodeRepoClient()
    results = client.search("main", repository="https://gitlab.com/owner/myrepo", max_results=5)
    assert results[0].url == "https://searchcode.com/file/999/src/main.py#L1"


@respx.mock
def test_searchcode_repo_empty_results():
    """Empty results list returns empty list without error."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = SearchcodeRepoClient()
    results = client.search("nothing", repository=_REPO_URL, max_results=5)
    assert results == []


@respx.mock
def test_searchcode_repo_http_error():
    """HTTP 4xx/5xx raises httpx.HTTPStatusError."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    client = SearchcodeRepoClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("query", repository=_REPO_URL)


@respx.mock
def test_searchcode_repo_sends_client_tag():
    """Request includes ?client=monster-search query param."""
    route = respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = SearchcodeRepoClient()
    client.search("query", repository=_REPO_URL, max_results=1)
    assert route.called
    request = route.calls.last.request
    assert "client=monster-search" in str(request.url)


@respx.mock
def test_searchcode_repo_sends_repository_in_body():
    """Request body includes repository field."""
    import json

    route = respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = SearchcodeRepoClient()
    client.search("query", repository=_REPO_URL, max_results=1)
    body = json.loads(route.calls.last.request.content)
    assert body["repository"] == _REPO_URL
    assert body["query"] == "query"


@pytest.mark.asyncio
@respx.mock
async def test_searchcode_repo_async():
    """asearch returns same shape as sync search."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    client = SearchcodeRepoClient()
    results = await client.asearch("import httpx", repository=_REPO_URL, max_results=5)
    assert len(results) == 3
    assert all(r.source == "searchcode_repo" for r in results)


@respx.mock
def test_searchcode_repo_snippet_content():
    """Snippet field contains the matching line content."""
    respx.post("https://api.searchcode.com/api/v1/code_search").mock(
        return_value=httpx.Response(200, json=_MOCK_RESPONSE)
    )
    client = SearchcodeRepoClient()
    results = client.search("import httpx", repository=_REPO_URL, max_results=10)
    assert results[0].snippet == "import httpx"
    assert results[1].snippet == "client = httpx.Client()"
