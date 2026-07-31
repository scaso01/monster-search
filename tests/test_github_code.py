"""Tests for GitHub Code Search client (gh search code CLI)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monster_search.clients.github_code import GithubCodeClient
from monster_search.models import SearchResult


GH_RESPONSE = json.dumps([
    {
        "path": "src/main.go",
        "repository": {"nameWithOwner": "owner/repo"},
        "textMatches": [{"fragment": "func main() {"}],
    },
    {
        "path": "cmd/server.go",
        "repository": {"nameWithOwner": "another/project"},
        "textMatches": [{"fragment": "func serve() error {"}],
    },
])


@patch("monster_search.clients.github_code.subprocess.run")
def test_github_code_search(mock_run: MagicMock) -> None:
    """Basic search returns SearchResults with correct fields."""
    mock_run.return_value = MagicMock(returncode=0, stdout=GH_RESPONSE, stderr="")

    client = GithubCodeClient()
    results = client.search("func main", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    # First result
    assert results[0].source == "github_code"
    assert "github.com" in results[0].url
    assert "owner/repo" in results[0].title
    assert "src/main.go" in results[0].title
    assert "func main() {" in results[0].snippet
    # Second result
    assert results[1].source == "github_code"
    assert "another/project" in results[1].url
    assert "cmd/server.go" in results[1].title


@patch("monster_search.clients.github_code.subprocess.run")
def test_github_code_empty(mock_run: MagicMock) -> None:
    """Empty JSON array returns empty list."""
    mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

    client = GithubCodeClient()
    results = client.search("xyznonexistent123")
    assert results == []


@patch("monster_search.clients.github_code.subprocess.run")
def test_github_code_error(mock_run: MagicMock) -> None:
    """Non-zero exit code raises RuntimeError."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="gh: authentication required"
    )

    client = GithubCodeClient()
    with pytest.raises(RuntimeError, match="gh search code failed"):
        client.search("test")


@patch("monster_search.clients.github_code.subprocess.run")
def test_github_code_timeout(mock_run: MagicMock) -> None:
    """subprocess.TimeoutExpired raises RuntimeError."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["gh"], timeout=15)

    client = GithubCodeClient()
    with pytest.raises(RuntimeError, match="timed out"):
        client.search("test")


@patch("monster_search.clients.github_code.subprocess.run")
def test_github_code_command_format(mock_run: MagicMock) -> None:
    """Verify the command list has correct structure."""
    mock_run.return_value = MagicMock(returncode=0, stdout="[]", stderr="")

    client = GithubCodeClient()
    client.search("func main", max_results=10)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gh"
    assert cmd[1] == "search"
    assert cmd[2] == "code"
    assert cmd[3] == "func main"
    assert "--json" in cmd
    assert "--limit" in cmd
    assert "10" in cmd


@patch("asyncio.create_subprocess_exec")
async def test_github_code_async_search(mock_exec: MagicMock) -> None:
    """Async search returns correct results."""
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (GH_RESPONSE.encode(), b"")
    mock_proc.returncode = 0
    mock_proc.kill = AsyncMock()
    mock_proc.wait = AsyncMock()
    mock_exec.return_value = mock_proc

    client = GithubCodeClient()
    results = await client.asearch("func main", max_results=5)

    assert len(results) == 2
    assert results[0].source == "github_code"
    assert "owner/repo" in results[0].title
    assert "github.com" in results[0].url
