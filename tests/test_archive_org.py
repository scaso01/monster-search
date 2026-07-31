"""Tests for ArchiveOrgClient.

The client has two routes. By default it calls archive.org directly over HTTP.
When MONSTER_SSH_HOST is set it instead curls from that host, which is the
workaround for exit IPs archive.org rate-limits. Most tests here cover the SSH
route and so set the env var via an autouse fixture; the direct-HTTP tests opt
out by passing an explicit Config with no ssh_host.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from monster_search.clients.archive_org import (
    ADVANCED_SEARCH_BASE,
    ArchiveOrgClient,
    _build_advanced_ssh_command,
    _url_query_to_text,
)
from monster_search.config import Config
from monster_search.models import SearchResult

SSH_HOST = "testhost.example"


@pytest.fixture(autouse=True)
def _route_via_ssh(_clean_monster_env, monkeypatch):
    """Point the client at the SSH route by default.

    Declared after _clean_monster_env (and depending on it) so the env var
    survives that fixture's MONSTER_* purge.
    """
    monkeypatch.setenv("MONSTER_SSH_HOST", SSH_HOST)

# --- Mock SSH / curl response payloads ---

ADVANCED_RESPONSE = {
    "responseHeader": {"status": 0},
    "response": {
        "numFound": 2,
        "start": 0,
        "docs": [
            {
                "identifier": "python-tutorial-2023",
                "title": "Python Tutorial 2023",
                "description": "A comprehensive Python tutorial.",
                "date": "2023-01-15T00:00:00Z",
                "publicdate": "2023-01-16T00:00:00Z",
                "mediatype": "texts",
            },
            {
                "identifier": "learn-python",
                "title": "Learn Python",
                "description": ["Part 1 of the series.", "Part 2 of the series."],
                "date": "2022-06-01T00:00:00Z",
                "publicdate": "2022-06-02T00:00:00Z",
                "mediatype": "texts",
            },
        ],
    },
}

ADVANCED_JSON = json.dumps(ADVANCED_RESPONSE)
EMPTY_RESPONSE_JSON = json.dumps({"response": {"numFound": 0, "docs": []}})


def _make_completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    """Return a fake CompletedProcess for mocking subprocess.run."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def _make_async_proc(stdout: bytes, returncode: int = 0):
    """Return a fake asyncio.Process for mocking create_subprocess_exec."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# === SSH command builder tests ===


def test_build_ssh_command_structure():
    """SSH command has correct shape: ssh flags + host + remote curl cmd."""
    cmd = _build_advanced_ssh_command("python tutorial", 5, 60, SSH_HOST)
    assert cmd[0] == "ssh"
    assert SSH_HOST in cmd
    # Last element is the remote command string
    remote = cmd[-1]
    assert "curl" in remote
    assert "archive.org/advancedsearch.php" in remote
    assert "python+tutorial" in remote  # URL-encoded space
    assert "rows=5" in remote


def test_build_ssh_command_url_encodes_query():
    """Queries with special characters are URL-encoded, not shell-quoted."""
    cmd = _build_advanced_ssh_command("rust async runtime: tokio", 3, 30, SSH_HOST)
    remote = cmd[-1]
    # The query params portion must be percent-encoded (no unquoted spaces or
    # bare colons in the URL query string itself).  shlex.quote wraps the whole
    # URL in outer single-quotes for the remote shell — that trailing quote is
    # expected and correct; strip it before asserting.
    url_portion = remote.split("archive.org")[1].rstrip("'")
    assert "'" not in url_portion  # no bare single-quotes inside the URL
    assert "rust" in remote
    assert "rust+async" in remote or "rust%20async" in remote  # space is encoded


def test_build_ssh_command_remote_timeout_less_than_outer():
    """Remote curl timeout is shorter than outer SSH timeout."""
    cmd = _build_advanced_ssh_command("test", 5, 60, SSH_HOST)
    remote = cmd[-1]
    # Remote timeout should be 57 (60-3), outer is 60
    assert "57" in remote


# === URL → text conversion ===


def test_url_query_to_text_strips_scheme():
    assert _url_query_to_text("https://example.org/") == "example.org"


def test_url_query_to_text_includes_path():
    assert _url_query_to_text("https://example.org/foo/bar") == "example.org foo/bar"


def test_url_query_to_text_root_path_only_domain():
    assert _url_query_to_text("http://python.org/") == "python.org"


# === Sync search tests (mocking subprocess.run) ===


@patch("monster_search.clients.archive_org.subprocess.run")
def test_advanced_search_text_query(mock_run):
    """Text queries call Advanced Search via SSH and parse results."""
    mock_run.return_value = _make_completed_process(ADVANCED_JSON)
    client = ArchiveOrgClient()
    results = client.search("python tutorial")
    assert len(results) == 2
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "archive_org"
    assert results[0].title == "Python Tutorial 2023"
    assert "archive.org/details/python-tutorial-2023" in results[0].url
    assert results[0].category == "texts"
    assert results[0].published == "2023-01-15T00:00:00Z"
    mock_run.assert_called_once()


@patch("monster_search.clients.archive_org.subprocess.run")
def test_url_query_falls_back_to_advanced_search(mock_run):
    """URL queries fall back to Advanced Search (CDX unavailable via VPN)."""
    mock_run.return_value = _make_completed_process(ADVANCED_JSON)
    client = ArchiveOrgClient()
    results = client.search("https://example.org/")
    # Confirm SSH was called and returned Advanced Search results
    assert mock_run.called
    # The SSH command's remote portion should contain the domain, not CDX URL
    cmd = mock_run.call_args[0][0]
    remote = cmd[-1]
    assert "cdx" not in remote
    assert "advancedsearch" in remote
    assert len(results) == 2


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_respects_max_results(mock_run):
    """max_results slices the parsed result list."""
    mock_run.return_value = _make_completed_process(ADVANCED_JSON)
    client = ArchiveOrgClient()
    results = client.search("python tutorial", max_results=1)
    assert len(results) == 1


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_list_description_joined(mock_run):
    """List-type descriptions are joined into a single string."""
    mock_run.return_value = _make_completed_process(ADVANCED_JSON)
    client = ArchiveOrgClient()
    results = client.search("python tutorial")
    assert "Part 1" in results[1].snippet
    assert "Part 2" in results[1].snippet


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_empty_results(mock_run):
    """Empty docs list returns an empty list."""
    mock_run.return_value = _make_completed_process(EMPTY_RESPONSE_JSON)
    client = ArchiveOrgClient()
    results = client.search("xyznonexistent")
    assert results == []


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_ssh_failure_raises_runtime_error(mock_run):
    """Non-zero SSH exit with no stdout raises RuntimeError."""
    mock_run.return_value = _make_completed_process("", returncode=255)
    client = ArchiveOrgClient()
    with pytest.raises(RuntimeError, match="archive_org SSH failed"):
        client.search("test")


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_non_json_response_raises_runtime_error(mock_run):
    """Non-JSON stdout raises RuntimeError."""
    mock_run.return_value = _make_completed_process("<html>Error</html>")
    client = ArchiveOrgClient()
    with pytest.raises(RuntimeError, match="non-JSON"):
        client.search("test")


@patch("monster_search.clients.archive_org.subprocess.run")
def test_search_timeout_raises_runtime_error(mock_run):
    """subprocess.TimeoutExpired is converted to RuntimeError."""
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ssh"], timeout=60)
    client = ArchiveOrgClient()
    with pytest.raises(RuntimeError, match="timed out"):
        client.search("test")


# === Default route: direct HTTP, no SSH host configured ===


def _direct_config(**kwargs) -> Config:
    """Config with the SSH route explicitly switched off."""
    return Config(ssh_host="", **kwargs)


@respx.mock
@patch("monster_search.clients.archive_org.subprocess.run")
def test_no_ssh_host_queries_archive_org_directly(mock_run):
    """With no ssh_host the client uses HTTP and never shells out.

    This is the path a fresh install takes. It used to be unreachable: every
    search shelled out to SSH, so anyone without the remote host got nothing.
    """
    route = respx.get(url__startswith=ADVANCED_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json=ADVANCED_RESPONSE)
    )
    results = ArchiveOrgClient(config=_direct_config()).search("python tutorial")
    assert route.called
    mock_run.assert_not_called()
    assert [r.title for r in results] == ["Python Tutorial 2023", "Learn Python"]
    assert "q=python+tutorial" in str(route.calls[0].request.url)


@respx.mock
def test_direct_http_url_query_searches_the_domain():
    """URL queries are converted to a text search on the direct route too."""
    route = respx.get(url__startswith=ADVANCED_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json=ADVANCED_RESPONSE)
    )
    ArchiveOrgClient(config=_direct_config()).search("https://example.org/foo")
    sent = str(route.calls[0].request.url)
    assert "example.org" in sent
    assert "cdx" not in sent


@respx.mock
def test_direct_http_error_status_raises():
    """An HTTP error is surfaced, not swallowed into an empty result list."""
    respx.get(url__startswith=ADVANCED_SEARCH_BASE).mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(httpx.HTTPStatusError):
        ArchiveOrgClient(config=_direct_config()).search("test")


@respx.mock
def test_direct_http_async_search():
    """The async entry point takes the same direct route."""
    respx.get(url__startswith=ADVANCED_SEARCH_BASE).mock(
        return_value=httpx.Response(200, json=ADVANCED_RESPONSE)
    )
    results = asyncio.run(
        ArchiveOrgClient(config=_direct_config()).asearch("python tutorial")
    )
    assert len(results) == 2
    assert results[0].source == "archive_org"


@patch("monster_search.clients.archive_org.subprocess.run")
def test_custom_timeout_config(mock_run):
    """Custom archive_org_timeout from Config is forwarded to subprocess."""
    mock_run.return_value = _make_completed_process(ADVANCED_JSON)
    config = Config(archive_org_timeout=30)
    client = ArchiveOrgClient(config=config)
    results = client.search("test")
    assert len(results) == 2
    _, kwargs = mock_run.call_args
    assert kwargs.get("timeout") == 30


# === Async search tests (mocking asyncio.create_subprocess_exec) ===


@pytest.mark.asyncio
@patch("monster_search.clients.archive_org.asyncio.create_subprocess_exec")
async def test_async_advanced_search(mock_exec):
    """Async text query returns parsed results."""
    mock_exec.return_value = _make_async_proc(ADVANCED_JSON.encode())
    client = ArchiveOrgClient()
    results = await client.asearch("python tutorial")
    assert len(results) == 2
    assert results[0].source == "archive_org"
    assert results[0].title == "Python Tutorial 2023"


@pytest.mark.asyncio
@patch("monster_search.clients.archive_org.asyncio.create_subprocess_exec")
async def test_async_url_query_uses_advanced_search(mock_exec):
    """Async URL query falls back to Advanced Search."""
    mock_exec.return_value = _make_async_proc(ADVANCED_JSON.encode())
    client = ArchiveOrgClient()
    results = await client.asearch("https://example.org/")
    assert len(results) == 2
    # Verify CDX not used
    call_args = mock_exec.call_args[0]
    remote_cmd = call_args[-1]
    assert "cdx" not in remote_cmd
    assert "advancedsearch" in remote_cmd


@pytest.mark.asyncio
@patch("monster_search.clients.archive_org.asyncio.create_subprocess_exec")
async def test_async_search_empty(mock_exec):
    """Async empty results return empty list."""
    mock_exec.return_value = _make_async_proc(EMPTY_RESPONSE_JSON.encode())
    client = ArchiveOrgClient()
    results = await client.asearch("xyznonexistent")
    assert results == []


@pytest.mark.asyncio
@patch("monster_search.clients.archive_org.asyncio.create_subprocess_exec")
async def test_async_search_ssh_failure_raises_runtime_error(mock_exec):
    """Async non-zero exit with empty stdout raises RuntimeError."""
    proc = _make_async_proc(b"", returncode=255)
    mock_exec.return_value = proc
    client = ArchiveOrgClient()
    with pytest.raises(RuntimeError, match="archive_org SSH failed"):
        await client.asearch("test")


@pytest.mark.asyncio
@patch("monster_search.clients.archive_org.asyncio.create_subprocess_exec")
async def test_async_search_timeout_raises_runtime_error(mock_exec):
    """Async timeout raises RuntimeError and kills process."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    mock_exec.return_value = proc
    client = ArchiveOrgClient()
    with pytest.raises(RuntimeError, match="timed out"):
        await client.asearch("test")
    proc.kill.assert_called_once()


# === URL Detection ===


def test_is_url_detection():
    """_is_url correctly identifies URLs vs text queries."""
    client = ArchiveOrgClient()
    assert client._is_url("https://example.org/") is True
    assert client._is_url("http://example.org/") is True
    assert client._is_url("python tutorial") is False
    assert client._is_url("archive.org") is False


# === Integration Tests ===


@pytest.mark.integration
def test_archive_org_live_text_search():
    """Live smoke test: text query, direct or via the configured SSH host."""
    client = ArchiveOrgClient()
    try:
        results = client.search("python programming", max_results=3)
    except RuntimeError as exc:
        pytest.skip(f"Archive.org unavailable: {exc}")
    assert len(results) > 0
    assert all(r.source == "archive_org" for r in results)
    assert all(r.url for r in results)


@pytest.mark.integration
def test_archive_org_live_url_search():
    """Live smoke test: a URL query falls back to catalog search."""
    client = ArchiveOrgClient()
    try:
        results = client.search("https://python.org/", max_results=3)
    except RuntimeError as exc:
        pytest.skip(f"Archive.org unavailable: {exc}")
    assert len(results) > 0
    assert all(r.source == "archive_org" for r in results)
    assert all(r.url for r in results)
