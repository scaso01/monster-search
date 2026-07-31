"""Tests for GitHub Repos discovery client (gh search repos CLI)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monster_search.clients.github_repos import GithubReposClient
from monster_search.models import SearchResult


GH_REPOS_RESPONSE = json.dumps([
    {
        "fullName": "meilisearch/meilisearch",
        "description": "A lightning-fast search API",
        "stargazersCount": 45000,
        "url": "https://github.com/meilisearch/meilisearch",
        "language": "Rust",
        "updatedAt": "2026-04-01T12:00:00Z",
    },
    {
        "fullName": "typesense/typesense",
        "description": "Open Source alternative to Algolia",
        "stargazersCount": 18000,
        "url": "https://github.com/typesense/typesense",
        "language": "C++",
        "updatedAt": "2026-03-28T08:00:00Z",
    },
])


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_search(mock_run: MagicMock) -> None:
    """Basic search returns SearchResults with correct fields."""
    mock_run.return_value = MagicMock(returncode=0, stdout=GH_REPOS_RESPONSE, stderr="")

    client = GithubReposClient()
    results = client.search("search engine", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    # First result
    assert results[0].source == "github_repos"
    assert results[0].title == "meilisearch/meilisearch"
    assert results[0].url == "https://github.com/meilisearch/meilisearch"
    assert "45,000 stars" in results[0].snippet
    assert results[0].category == "Rust"
    assert results[0].published == "2026-04-01"
    # Second result
    assert results[1].source == "github_repos"
    assert results[1].title == "typesense/typesense"
    assert results[1].category == "C++"


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_empty(mock_run: MagicMock) -> None:
    """Empty JSON array returns empty list."""
    mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

    client = GithubReposClient()
    results = client.search("xyznonexistent123")
    assert results == []


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_error(mock_run: MagicMock) -> None:
    """Non-zero exit code raises RuntimeError."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="gh: authentication required"
    )

    client = GithubReposClient()
    with pytest.raises(RuntimeError, match="gh search repos failed"):
        client.search("test")


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_timeout(mock_run: MagicMock) -> None:
    """subprocess.TimeoutExpired raises RuntimeError."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["gh"], timeout=15)

    client = GithubReposClient()
    with pytest.raises(RuntimeError, match="timed out"):
        client.search("test")


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_command_format(mock_run: MagicMock) -> None:
    """Verify the command list has correct structure."""
    mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

    client = GithubReposClient()
    client.search("search engine", max_results=10)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gh"
    assert cmd[1] == "search"
    assert cmd[2] == "repos"
    # Multi-word query is split into individual tokens for gh search repos
    assert cmd[3] == "search"
    assert cmd[4] == "engine"
    assert "--json" in cmd
    assert "--limit" in cmd
    assert "10" in cmd


@patch("asyncio.create_subprocess_exec")
async def test_github_repos_async_search(mock_exec: MagicMock) -> None:
    """Async search returns correct results."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (GH_REPOS_RESPONSE.encode(), b"")
    mock_proc.returncode = 0
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()
    mock_exec.return_value = mock_proc

    client = GithubReposClient()
    results = await client.asearch("search engine", max_results=5)

    assert len(results) == 2
    assert results[0].source == "github_repos"
    assert results[0].title == "meilisearch/meilisearch"
    assert results[0].url == "https://github.com/meilisearch/meilisearch"


@patch("monster_search.clients.github_repos.subprocess.run")
def test_github_repos_snippet_format(mock_run: MagicMock) -> None:
    """Snippet contains description, stars, and language."""
    mock_run.return_value = MagicMock(returncode=0, stdout=GH_REPOS_RESPONSE, stderr="")

    client = GithubReposClient()
    results = client.search("search engine", max_results=5)

    snippet = results[0].snippet
    assert "A lightning-fast search API" in snippet
    assert "45,000 stars" in snippet
    assert "Rust" in snippet
