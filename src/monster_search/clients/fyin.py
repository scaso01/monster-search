"""Fyin search client — runs the fyin CLI on a remote host via SSH."""

from __future__ import annotations

import asyncio
import re
import shlex
import subprocess

from monster_search.config import Config
from monster_search.models import SearchResult

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def _extract_urls(text: str) -> list[SearchResult]:
    """Extract URLs from fyin output text as SearchResult items."""
    seen: set[str] = set()
    results: list[SearchResult] = []
    for url in _URL_RE.findall(text):
        # Clean trailing punctuation
        url = url.rstrip(".,;:!?)")
        if url in seen:
            continue
        seen.add(url)
        results.append(
            SearchResult(
                title=url.split("/")[-1] or url,
                url=url,
                snippet="",
                source="fyin",
            )
        )
    return results


def _build_ssh_command(
    query: str, timeout: int, ssh_host: str, env_file: str = ""
) -> list[str]:
    """Build the SSH command to run fyin on the configured remote host.

    Uses remote ``timeout`` to hard-kill fyin if it stalls (e.g. waiting on a
    saturated llama-server), and ``--search 1`` to limit scraped content so the
    LLM prompt stays small and generation finishes within budget.
    """
    escaped_query = shlex.quote(query)
    # Remote timeout is slightly less than the SSH-side timeout so fyin gets
    # killed remotely before the SSH subprocess is killed locally, giving us
    # stderr diagnostics instead of a bare timeout.
    remote_timeout = max(timeout - 15, 30)
    remote_cmd = f"timeout {remote_timeout} fyin --query {escaped_query} --search 1"
    if env_file:
        # fyin needs API keys in its environment; sourcing is opt-in because
        # the file lives wherever the operator put it.
        remote_cmd = f"set -a && . {shlex.quote(env_file)} && set +a && {remote_cmd}"
    return [
        "ssh",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        ssh_host,
        remote_cmd,
    ]


class FyinClient:
    """Client for Fyin search, run over SSH on a remote host."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _command(self, query: str) -> list[str]:
        """Build the SSH command, refusing to run when no host is configured."""
        if not self._config.ssh_host:
            raise RuntimeError(
                "fyin needs a remote host: set MONSTER_SSH_HOST (e.g. user@host) "
                "to a machine with fyin installed."
            )
        return _build_ssh_command(
            query,
            self._config.fyin_timeout,
            self._config.ssh_host,
            self._config.fyin_env_file,
        )

    def search(self, query: str) -> tuple[str, list[SearchResult]]:
        """Synchronous search by running fyin on the remote host over SSH."""
        cmd = self._command(query)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._config.fyin_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run with capture_output already kills the child on
            # timeout, but we still need to convert the exception.
            raise RuntimeError(
                f"fyin timed out after {self._config.fyin_timeout}s"
            ) from exc
        output = _strip_ansi(result.stdout)
        if result.returncode != 0 and not output:
            raise RuntimeError(
                f"fyin exited with code {result.returncode}: {result.stderr[:500]}"
            )
        return output, _extract_urls(output)

    async def asearch(self, query: str) -> tuple[str, list[SearchResult]]:
        """Async search by running fyin on the remote host over SSH."""
        cmd = self._command(query)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._config.fyin_timeout,
            )
        except asyncio.TimeoutError:
            # Kill the SSH subprocess so it doesn't keep a llama-server slot
            # occupied after we've given up waiting.
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"fyin timed out after {self._config.fyin_timeout}s")
        output = _strip_ansi(stdout.decode())
        if proc.returncode != 0 and not output:
            raise RuntimeError(
                f"fyin exited with code {proc.returncode}: {stderr.decode()[:500]}"
            )
        return output, _extract_urls(output)
