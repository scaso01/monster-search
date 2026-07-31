from __future__ import annotations

import base64

import httpx
import pytest
import respx

from monster_search.clients.zoekt import ZoektClient
from monster_search.config import Config
from monster_search.models import SearchResult

LINE_1 = base64.b64encode(b"func main() {").decode()
LINE_2 = base64.b64encode(b'    fmt.Println("hello")').decode()
LINE_3 = base64.b64encode(b"}").decode()
LINE_4 = base64.b64encode(b"package main").decode()

MOCK_RESPONSE = {
    "RepoURLs": {
        "my-org/my-repo": "https://github.com/my-org/my-repo",
    },
    "Files": [
        {
            "FileName": "cmd/server/main.go",
            "Repository": "my-org/my-repo",
            "Version": "abc123",
            "Language": "Go",
            "Branches": ["main"],
            "Score": 12.5,
            "LineMatches": [
                {"Line": LINE_1, "LineNumber": 5, "LineFragments": []},
                {"Line": LINE_2, "LineNumber": 6, "LineFragments": []},
                {"Line": LINE_3, "LineNumber": 7, "LineFragments": []},
                {"Line": LINE_4, "LineNumber": 1, "LineFragments": []},
            ],
        },
        {
            "FileName": "pkg/util/helper.go",
            "Repository": "my-org/my-repo",
            "Version": "abc123",
            "Language": "Go",
            "Branches": ["main"],
            "Score": 8.3,
            "LineMatches": [
                {"Line": LINE_1, "LineNumber": 10, "LineFragments": []},
            ],
        },
    ],
}


@respx.mock
def test_zoekt_search():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = ZoektClient()
    results = client.search("func main")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].title == "cmd/server/main.go"
    assert results[0].source == "zoekt"
    assert results[0].score == 12.5
    assert results[0].category == "Go"
    assert results[0].url == "https://github.com/my-org/my-repo/blob/abc123/cmd/server/main.go"
    # Only first 3 line matches in snippet
    assert "L5:" in results[0].snippet
    assert "L6:" in results[0].snippet
    assert "L7:" in results[0].snippet
    assert "L1:" not in results[0].snippet
    assert "func main()" in results[0].snippet


@respx.mock
def test_zoekt_search_max_results():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = ZoektClient()
    results = client.search("func main", max_results=1)
    assert len(results) == 1


@respx.mock
def test_zoekt_search_custom_config():
    respx.post("http://localhost:7070/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(zoekt_url="http://localhost:7070")
    client = ZoektClient(config=config)
    results = client.search("test")
    assert len(results) == 2


@respx.mock
def test_zoekt_search_error():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(500)
    )
    client = ZoektClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_zoekt_search_empty():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json={"Files": None, "RepoURLs": {}})
    )
    client = ZoektClient()
    results = client.search("nonexistent")
    assert results == []


@respx.mock
@pytest.mark.asyncio
async def test_zoekt_async_search():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = ZoektClient()
    results = await client.asearch("func main")
    assert len(results) == 2
    assert results[0].source == "zoekt"
    assert results[0].title == "cmd/server/main.go"


@respx.mock
def test_zoekt_sends_correct_body():
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json={"Files": [], "RepoURLs": {}})
    )
    client = ZoektClient()
    client.search("my query", max_results=10)
    request = respx.calls[0].request
    import json

    body = json.loads(request.content)
    assert body["Q"] == "my query"
    assert body["Opts"]["MaxDocDisplayCount"] == 10
    assert body["Opts"]["NumContextLines"] == 1


@respx.mock
def test_zoekt_fallback_url_without_repo_urls():
    response = {
        "RepoURLs": {},
        "Files": [
            {
                "FileName": "main.rs",
                "Repository": "local/repo",
                "Version": "def456",
                "Language": "Rust",
                "Branches": ["main"],
                "Score": 5.0,
                "LineMatches": [
                    {"Line": base64.b64encode(b"fn main() {}").decode(), "LineNumber": 1, "LineFragments": []},
                ],
            },
        ],
    }
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json=response)
    )
    client = ZoektClient()
    results = client.search("fn main")
    assert len(results) == 1
    # Falls back to https://<Repository> when no usable RepoURLs entry
    assert results[0].url == "https://local/repo"
    assert results[0].category == "Rust"
