"""Archive.org search client (Advanced Search API).

Strategy
--------
All queries — including URL-style "give me snapshots of example.com" queries —
go through the Advanced Search API.  For URL queries we strip the scheme and
search the domain as a text query so the catalog returns relevant items.

Routing
-------
By default the request is made directly over HTTP, which is all most people
need.  archive.org does, however, rate-limit some VPN exit IPs hard: CDX
returns a persistent HTTP 429 and plain requests can TCP-time-out.  Setting
MONSTER_SSH_HOST routes the request through curl on that host instead, which
is the escape hatch when your own exit IP is one of the blocked ones.

The CDX path is preserved as dead code with a clear comment; it can be
re-enabled if archive.org ever lifts the VPN rate-limit on CDX.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import subprocess
from urllib.parse import urlencode, urlparse

import httpx

from monster_search.config import Config
from monster_search.models import SearchResult

ADVANCED_SEARCH_BASE = "https://archive.org/advancedsearch.php"
WAYBACK_BASE = "https://web.archive.org/web"

# Conservative timeout for the SSH round-trip; the remote curl is given 3s
# less so we get stderr diagnostics on failure instead of a bare timeout.
_SSH_CONNECT_TIMEOUT = 10


def _advanced_search_url(query: str, max_results: int) -> str:
    """Build the archive.org Advanced Search URL for a text query."""
    params = urlencode({
        "q": query,
        "output": "json",
        "rows": max_results,
        "fl[]": "identifier,title,description,date,publicdate,mediatype",
    })
    return f"{ADVANCED_SEARCH_BASE}?{params}"


def _build_advanced_ssh_command(
    query: str, max_results: int, ssh_timeout: int, ssh_host: str
) -> list[str]:
    """Build the SSH command that curls archive.org from the remote host.

    Mirrors the pattern in fyin._build_ssh_command: the remote ``timeout`` is
    set slightly shorter than the outer subprocess timeout so the far side can
    clean up before we give up on ours. The URL is shell-quoted as a single
    argument so the query cannot break out into the remote shell.
    """
    url = _advanced_search_url(query, max_results)
    remote_timeout = max(ssh_timeout - 3, 5)
    remote_cmd = f"timeout {remote_timeout} curl -s --max-time {remote_timeout} {shlex.quote(url)}"
    return [
        "ssh",
        "-o", f"ConnectTimeout={_SSH_CONNECT_TIMEOUT}",
        "-o", "StrictHostKeyChecking=no",
        ssh_host,
        remote_cmd,
    ]


def _url_query_to_text(query: str) -> str:
    """Convert a URL query into a text search term for the Advanced Search API.

    CDX (snapshot lookup) is unavailable via VPN exit IPs.  As a fallback we
    search the Internet Archive catalog for the domain + path so the user still
    gets catalog entries related to that URL.
    """
    parsed = urlparse(query)
    # Use netloc + path, drop scheme, port, and credentials
    text = parsed.netloc or query
    if parsed.path and parsed.path != "/":
        text = f"{text} {parsed.path.strip('/')}"
    return text


class ArchiveOrgClient:
    """Client for Archive.org search APIs.

    Only the Advanced Search endpoint is used; CDX returns HTTP 429 from the
    VPN exits we can reach.  Requests go out directly unless MONSTER_SSH_HOST
    is set, in which case they are curled from that host instead.

    Two logical modes are still supported:
    - Text query  → Advanced Search catalog full-text search
    - URL query   → domain/path converted to text, then Advanced Search catalog
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _is_url(self, query: str) -> bool:
        """Detect if query looks like a URL."""
        return query.startswith("http://") or query.startswith("https://")

    def _effective_query(self, query: str) -> str:
        """Return the search term to send to the Advanced Search API."""
        if self._is_url(query):
            return _url_query_to_text(query)
        return query

    def _parse_advanced_results(self, data: dict, max_results: int) -> list[SearchResult]:
        """Parse Advanced Search JSON response into SearchResults."""
        results = []
        docs = data.get("response", {}).get("docs", [])
        for item in docs[:max_results]:
            identifier = item.get("identifier", "")
            title = item.get("title", identifier)
            url = f"https://archive.org/details/{identifier}" if identifier else ""
            description = item.get("description", "")
            if isinstance(description, list):
                description = " ".join(description)
            date = item.get("date", item.get("publicdate", ""))
            mediatype = item.get("mediatype", "")
            snippet = description[:500] if description else f"Media type: {mediatype}"
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="archive_org",
                    category=mediatype or None,
                    published=date or None,
                )
            )
        return results

    def _run_http(self, query: str, max_results: int) -> list[SearchResult]:
        """Fetch Advanced Search directly over HTTP."""
        timeout = self._config.archive_org_timeout
        response = httpx.get(_advanced_search_url(query, max_results), timeout=timeout)
        response.raise_for_status()
        return self._parse_advanced_results(response.json(), max_results)

    async def _run_http_async(self, query: str, max_results: int) -> list[SearchResult]:
        """Fetch Advanced Search directly over HTTP, asynchronously."""
        timeout = self._config.archive_org_timeout
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(_advanced_search_url(query, max_results))
        response.raise_for_status()
        return self._parse_advanced_results(response.json(), max_results)

    def _run_ssh(self, query: str, max_results: int) -> list[SearchResult]:
        """Execute the SSH curl command synchronously and parse the result."""
        timeout = self._config.archive_org_timeout
        cmd = _build_advanced_ssh_command(
            query, max_results, timeout, self._config.ssh_host
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"archive_org SSH timed out after {timeout}s"
            ) from exc

        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"archive_org SSH failed (exit {result.returncode}): "
                f"{result.stderr[:300]}"
            )

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"archive_org returned non-JSON: {result.stdout[:200]!r}"
            ) from exc

        return self._parse_advanced_results(data, max_results)

    async def _run_ssh_async(self, query: str, max_results: int) -> list[SearchResult]:
        """Execute the SSH curl command asynchronously and parse the result."""
        timeout = self._config.archive_org_timeout
        cmd = _build_advanced_ssh_command(
            query, max_results, timeout, self._config.ssh_host
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"archive_org SSH timed out after {timeout}s")

        stdout_text = stdout.decode()
        if proc.returncode != 0 and not stdout_text.strip():
            raise RuntimeError(
                f"archive_org SSH failed (exit {proc.returncode}): "
                f"{stderr.decode()[:300]}"
            )

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"archive_org returned non-JSON: {stdout_text[:200]!r}"
            ) from exc

        return self._parse_advanced_results(data, max_results)

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous Advanced Search, direct or proxied over SSH.

        Both text queries and URL queries go through Advanced Search. When
        MONSTER_SSH_HOST is set the request is curled from that host instead of
        made locally, which is the workaround for exit IPs archive.org blocks.
        """
        max_results = max_results or self._config.max_results
        effective = self._effective_query(query)
        if self._config.ssh_host:
            return self._run_ssh(effective, max_results)
        return self._run_http(effective, max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async Advanced Search, direct or proxied over SSH.

        Both text queries and URL queries go through Advanced Search. When
        MONSTER_SSH_HOST is set the request is curled from that host instead of
        made locally, which is the workaround for exit IPs archive.org blocks.
        """
        max_results = max_results or self._config.max_results
        effective = self._effective_query(query)
        if self._config.ssh_host:
            return await self._run_ssh_async(effective, max_results)
        return await self._run_http_async(effective, max_results)


# ---------------------------------------------------------------------------
# CDX path (preserved — currently unusable from VPN exit IPs)
# ---------------------------------------------------------------------------
# The CDX endpoint (web.archive.org/cdx/search/cdx) returns a persistent HTTP
# 429 from VPN exit IPs, and TCP-times-out from others.  The code below is kept
# for reference and can be re-enabled if archive.org lifts the VPN blocks.
#
# def _search_cdx(client, url, max_results): ...
# def _parse_cdx_results(rows, max_results): ...
# ---------------------------------------------------------------------------
