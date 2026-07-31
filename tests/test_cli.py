from __future__ import annotations

import json
import httpx
import pytest
import respx

from monster_search.cli import main


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Prevent main() from loading .env during tests."""
    monkeypatch.setattr("monster_search.cli.load_dotenv", lambda *a, **kw: False)


MOCK_SEARXNG = {
    "query": "test",
    "number_of_results": 1,
    "results": [
        {
            "url": "https://example.com",
            "title": "Example",
            "content": "Test content",
            "engine": "duckduckgo",
            "engines": ["duckduckgo"],
            "score": 5.0,
            "category": "general",
            "publishedDate": None,
            "positions": [1],
        }
    ],
    "suggestions": [],
    "answers": [],
    "infoboxes": [],
    "corrections": [],
    "unresponsive_engines": [],
}


def test_cli_default_search(capsys, monkeypatch):
    """Default mode calls smart_search."""
    from unittest.mock import AsyncMock, patch as _patch
    from monster_search.clients.all_engines import AllEnginesClient
    from monster_search.models import SearchResult

    mock_results = [SearchResult(
        title="Example", url="https://example.com",
        snippet="Test content", source="searxng",
    )]
    mock_smart = AsyncMock(return_value=("Smart search [general]: 1 results", "", mock_results))

    monkeypatch.setattr("sys.argv", ["monster-search", "test query"])
    with _patch.object(AllEnginesClient, "smart_search", mock_smart):
        main()

    output = capsys.readouterr().out
    assert "Example" in output
    assert "https://example.com" in output
    mock_smart.assert_called_once()


@respx.mock
def test_cli_json_output(capsys, monkeypatch):
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json=MOCK_SEARXNG)
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--json", "test query"])
    main()
    output = capsys.readouterr().out
    data = json.loads(output)
    assert "results" in data
    assert data["results"][0]["title"] == "Example"


@respx.mock
def test_cli_health_check(capsys, monkeypatch):
    from unittest.mock import patch as _patch

    # Docker / self-hosted
    respx.get("http://localhost:8080/search", params__contains={"q": "health"}).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get("http://localhost:8300/ok").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("http://localhost:11235/health").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json={"domain_name": "example.com"})
    )
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json={"Stats": {}, "Files": []})
    )
    respx.get("http://localhost:3004/api/providers").mock(
        return_value=httpx.Response(200, json={"providers": []})
    )
    respx.get("http://localhost:42110/api/health").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("http://localhost:7700/health").mock(
        return_value=httpx.Response(200, json={"status": "available"})
    )
    # External APIs
    respx.get("https://www.perplexity.ai").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("https://export.arxiv.org/api/query", params__contains={"search_query": "all:test"}).mock(
        return_value=httpx.Response(200, text="<xml/>")
    )
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search", params__contains={"query": "test"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.get("https://api.openalex.org/works", params__contains={"search": "test"}).mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get("https://api.osv.dev/v1/vulns", params__contains={"page_token": ""}).mock(
        return_value=httpx.Response(200, json={"vulns": []})
    )
    respx.get("https://api.deps.dev/v3alpha/systems/npm/packages/express").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("https://news.google.com/rss").mock(
        return_value=httpx.Response(200, text="<xml/>")
    )
    respx.get("https://api.marginalia.nu/").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("https://archive.org").mock(
        return_value=httpx.Response(200, text="OK")
    )
    respx.get("https://grep.app").mock(
        return_value=httpx.Response(200, text="OK")
    )
    # Hacker News (Algolia)
    respx.get("https://hn.algolia.com/api/v1/search", params__contains={"query": "test"}).mock(
        return_value=httpx.Response(200, json={"hits": []})
    )
    # HuggingFace Hub
    respx.get("https://huggingface.co/api/models", params__contains={"search": "test"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    # Reddit
    respx.get("https://www.reddit.com/search.json").mock(
        return_value=httpx.Response(200, json={"data": {"children": []}})
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--health"])
    with _patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.return_value = type("R", (), {"returncode": 0})()
        main()
    output = capsys.readouterr().out
    assert "searxng" in output.lower()
    assert "local_researcher: UP" in output


@respx.mock
def test_cli_handles_http_error(capsys, monkeypatch):
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(500)
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--engine", "searxng", "test"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "HTTP 500" in err
    assert "searxng" in err


@respx.mock
def test_cli_handles_429(capsys, monkeypatch):
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(429)
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--engine", "searxng", "test"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "429" in err
    assert "rate limited" in err


@respx.mock
def test_cli_handles_timeout(capsys, monkeypatch):
    respx.get("http://localhost:8080/search").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--engine", "searxng", "test"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "timed out" in err


@respx.mock
def test_cli_handles_connection_error(capsys, monkeypatch):
    respx.get("http://localhost:8080/search").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    monkeypatch.setattr("sys.argv", ["monster-search", "--engine", "searxng", "test"])
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "cannot connect" in err
    assert "service running" in err


def test_cli_searchcode_repo_requires_repo_flag(capsys, monkeypatch):
    """--engine searchcode_repo without --repo exits 1 with a clear error."""
    monkeypatch.setattr(
        "sys.argv", ["monster-search", "--engine", "searchcode_repo", "import httpx"]
    )
    with pytest.raises(SystemExit, match="1"):
        main()
    err = capsys.readouterr().err
    assert "--repo" in err
    assert "searchcode_repo" in err
