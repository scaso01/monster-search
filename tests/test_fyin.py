"""Tests for the Fyin client, which shells out to a remote host over SSH."""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from monster_search.clients import fyin as fyin_mod
from monster_search.clients.fyin import (
    FyinClient,
    _build_ssh_command,
    _extract_urls,
    _strip_ansi,
)
from monster_search.config import Config
from monster_search.models import SearchResult


HOST_CONFIG = Config(ssh_host="user@searchbox")


class _CompletedProcess:
    """Stand-in for subprocess.CompletedProcess."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeAsyncProcess:
    """Stand-in for the object asyncio.create_subprocess_exec returns."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


# --- text helpers -------------------------------------------------------


def test_strip_ansi_removes_colour_codes():
    """fyin colours its output; the codes must not reach the snippet text."""
    assert _strip_ansi("\x1b[32mgreen\x1b[0m text") == "green text"


def test_strip_ansi_leaves_plain_text_alone():
    assert _strip_ansi("nothing to strip") == "nothing to strip"


def test_extract_urls_finds_and_titles_results():
    """Titles come from the last path segment."""
    results = _extract_urls("see https://example.com/some/page for more")

    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].url == "https://example.com/some/page"
    assert results[0].title == "page"
    assert results[0].source == "fyin"


def test_extract_urls_deduplicates():
    """A URL repeated in the output is returned once."""
    results = _extract_urls("https://a.example and again https://a.example")

    assert len(results) == 1


def test_extract_urls_strips_trailing_punctuation():
    """Prose punctuation must not end up inside the URL."""
    results = _extract_urls("Visit https://example.com/page.")

    assert results[0].url == "https://example.com/page"


def test_extract_urls_titles_bare_domain_with_the_url():
    """A URL with no path has no last segment to use as a title."""
    results = _extract_urls("https://example.com")

    assert results[0].title == "example.com" or results[0].title == "https://example.com"


def test_extract_urls_returns_empty_for_no_urls():
    assert _extract_urls("no links here at all") == []


# --- command construction -----------------------------------------------


def test_build_ssh_command_structure():
    """The command must be non-interactive and time-limited on both ends."""
    cmd = _build_ssh_command("rust async", timeout=300, ssh_host="user@searchbox")

    assert cmd[0] == "ssh"
    assert "ConnectTimeout=10" in cmd
    assert "StrictHostKeyChecking=no" in cmd
    assert "user@searchbox" in cmd
    remote = cmd[-1]
    assert "fyin --query" in remote
    assert "--search 1" in remote


def test_build_ssh_command_remote_timeout_is_below_the_local_one():
    """The remote kill must fire first so stderr survives for diagnostics."""
    remote = _build_ssh_command("q", timeout=300, ssh_host="h")[-1]

    assert remote.startswith("timeout 285 ")


def test_build_ssh_command_remote_timeout_has_a_floor():
    """A short local timeout must not produce a negative remote one."""
    remote = _build_ssh_command("q", timeout=20, ssh_host="h")[-1]

    assert remote.startswith("timeout 30 ")


def test_build_ssh_command_quotes_the_query():
    """A query with shell metacharacters must not be able to run commands."""
    remote = _build_ssh_command("foo; rm -rf /", timeout=300, ssh_host="h")[-1]

    assert "; rm -rf /" not in remote.replace("'foo; rm -rf /'", "")
    assert "'foo; rm -rf /'" in remote


def test_build_ssh_command_sources_env_file_when_set():
    """The env file is sourced before fyin runs so it sees its API keys."""
    remote = _build_ssh_command("q", timeout=300, ssh_host="h", env_file="/etc/fyin.env")[-1]

    assert remote.startswith("set -a && . /etc/fyin.env && set +a && ")


def test_build_ssh_command_omits_sourcing_when_env_file_unset():
    remote = _build_ssh_command("q", timeout=300, ssh_host="h")[-1]

    assert "set -a" not in remote


# --- the off-by-default guard -------------------------------------------


def test_search_refuses_to_run_without_a_configured_host():
    """An unset MONSTER_SSH_HOST must switch the engine off, not SSH somewhere."""
    with pytest.raises(RuntimeError, match="MONSTER_SSH_HOST"):
        FyinClient(config=Config(ssh_host="")).search("anything")


@pytest.mark.asyncio
async def test_async_search_refuses_without_a_configured_host():
    """Same guard on the async path."""
    with pytest.raises(RuntimeError, match="MONSTER_SSH_HOST"):
        await FyinClient(config=Config(ssh_host="")).asearch("anything")


# --- sync search --------------------------------------------------------


def test_search_returns_output_and_urls(monkeypatch):
    """Happy path: cleaned stdout plus the URLs found in it."""
    monkeypatch.setattr(
        fyin_mod.subprocess,
        "run",
        lambda *a, **k: _CompletedProcess(stdout="\x1b[32mFound\x1b[0m https://a.example"),
    )
    output, results = FyinClient(config=HOST_CONFIG).search("q")

    assert output == "Found https://a.example"
    assert [r.url for r in results] == ["https://a.example"]


def test_search_converts_timeout_to_runtime_error(monkeypatch):
    """A stalled fyin gives a readable error, not a raw subprocess exception."""

    def _raise(*a, **k):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=300)

    monkeypatch.setattr(fyin_mod.subprocess, "run", _raise)
    with pytest.raises(RuntimeError, match="fyin timed out after 300s"):
        FyinClient(config=HOST_CONFIG).search("q")


def test_search_raises_when_it_fails_with_no_output(monkeypatch):
    """A non-zero exit and nothing on stdout is a real failure."""
    monkeypatch.setattr(
        fyin_mod.subprocess,
        "run",
        lambda *a, **k: _CompletedProcess(stdout="", stderr="boom", returncode=1),
    )
    with pytest.raises(RuntimeError, match="fyin exited with code 1"):
        FyinClient(config=HOST_CONFIG).search("q")


def test_search_keeps_partial_output_despite_nonzero_exit(monkeypatch):
    """fyin killed by the remote timeout still leaves usable output."""
    monkeypatch.setattr(
        fyin_mod.subprocess,
        "run",
        lambda *a, **k: _CompletedProcess(
            stdout="partial https://a.example", stderr="killed", returncode=124
        ),
    )
    output, results = FyinClient(config=HOST_CONFIG).search("q")

    assert "partial" in output
    assert len(results) == 1


def test_search_passes_the_configured_timeout(monkeypatch):
    """The subprocess timeout must come from config, not a hardcoded value."""
    seen = {}

    def _capture(*a, **k):
        seen.update(k)
        return _CompletedProcess(stdout="ok")

    monkeypatch.setattr(fyin_mod.subprocess, "run", _capture)
    FyinClient(config=Config(ssh_host="h", fyin_timeout=42)).search("q")

    assert seen["timeout"] == 42


# --- async search -------------------------------------------------------


@pytest.mark.asyncio
async def test_async_search_returns_output_and_urls(monkeypatch):
    """Happy path on the async transport."""

    async def _fake_exec(*a, **k):
        return _FakeAsyncProcess(stdout=b"\x1b[32mFound\x1b[0m https://a.example")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    output, results = await FyinClient(config=HOST_CONFIG).asearch("q")

    assert output == "Found https://a.example"
    assert [r.url for r in results] == ["https://a.example"]


@pytest.mark.asyncio
async def test_async_search_kills_the_process_on_timeout(monkeypatch):
    """A timed-out SSH child must be killed so it frees its llama-server slot."""
    proc = _FakeAsyncProcess()

    async def _fake_exec(*a, **k):
        return proc

    async def _raise_timeout(awaitable, timeout=None):
        # Close the coroutine we are not awaiting, so there is no warning.
        awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(asyncio, "wait_for", _raise_timeout)

    with pytest.raises(RuntimeError, match="fyin timed out"):
        await FyinClient(config=HOST_CONFIG).asearch("q")

    assert proc.killed is True


@pytest.mark.asyncio
async def test_async_search_raises_when_it_fails_with_no_output(monkeypatch):
    """Non-zero exit with empty stdout raises on the async path too."""

    async def _fake_exec(*a, **k):
        return _FakeAsyncProcess(stdout=b"", stderr=b"boom", returncode=2)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    with pytest.raises(RuntimeError, match="fyin exited with code 2"):
        await FyinClient(config=HOST_CONFIG).asearch("q")


@pytest.mark.asyncio
async def test_async_search_keeps_partial_output(monkeypatch):
    """Partial output survives a non-zero exit."""

    async def _fake_exec(*a, **k):
        return _FakeAsyncProcess(
            stdout=b"partial https://a.example", stderr=b"killed", returncode=124
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)
    output, results = await FyinClient(config=HOST_CONFIG).asearch("q")

    assert "partial" in output
    assert len(results) == 1
