"""OSV.dev vulnerability database client."""

from __future__ import annotations

import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

OSV_API_BASE = "https://api.osv.dev/v1"

ECOSYSTEM_MAP = {
    "pypi": "PyPI",
    "npm": "npm",
    "cargo": "crates.io",
    "go": "Go",
    "maven": "Maven",
    "nuget": "NuGet",
    "rubygems": "RubyGems",
    "packagist": "Packagist",
    "hex": "Hex",
    "pub": "Pub",
}


class OsvClient:
    """Client for OSV.dev open-source vulnerability database."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _is_vuln_id(self, query: str) -> bool:
        """Check if query is a direct vulnerability ID (CVE-* or GHSA-*)."""
        upper = query.strip().upper()
        return upper.startswith("CVE-") or upper.startswith("GHSA-")

    def _parse_ecosystem(self, query: str) -> tuple[str, str]:
        """Parse 'ecosystem:package' format. Bare name defaults to PyPI."""
        if ":" in query:
            eco, name = query.split(":", 1)
            ecosystem = ECOSYSTEM_MAP.get(eco.lower(), eco)
            return ecosystem, name
        return "PyPI", query

    def _parse_results(self, vulns: list[dict], max_results: int) -> list[SearchResult]:
        results = []
        for vuln in vulns[:max_results]:
            vuln_id = vuln.get("id", "")
            summary = vuln.get("summary", "")
            details = vuln.get("details", "")
            published = vuln.get("published", "")
            references = vuln.get("references", [])
            affected = vuln.get("affected", [])

            # First reference URL or fallback
            url = ""
            for ref in references:
                if ref.get("url"):
                    url = ref["url"]
                    break
            if not url:
                url = f"https://osv.dev/vulnerability/{vuln_id}"

            # Determine ecosystem from affected packages
            ecosystem = ""
            if affected:
                pkg = affected[0].get("package", {})
                ecosystem = pkg.get("ecosystem", "")

            snippet = details[:500] if details else summary
            results.append(
                SearchResult(
                    title=f"{vuln_id}: {summary}",
                    url=url,
                    snippet=snippet,
                    source="osv",
                    published=published or None,
                    category=ecosystem or None,
                )
            )
        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous search. Routes CVE-*/GHSA-* to GET, else POST query."""
        max_results = max_results or self._config.max_results
        client = get_client(OSV_API_BASE, self._config.osv_timeout)
        if self._is_vuln_id(query):
            return self._search_by_id(client, query.strip(), max_results)
        return self._search_by_package(client, query, max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async search. Routes CVE-*/GHSA-* to GET, else POST query."""
        max_results = max_results or self._config.max_results
        client = get_async_client(OSV_API_BASE, self._config.osv_timeout)
        if self._is_vuln_id(query):
            return await self._asearch_by_id(client, query.strip(), max_results)
        return await self._asearch_by_package(client, query, max_results)

    def _search_by_id(
        self, client: httpx.Client, vuln_id: str, max_results: int
    ) -> list[SearchResult]:
        resp = client.get(f"{OSV_API_BASE}/vulns/{vuln_id}")
        resp.raise_for_status()
        return self._parse_results([resp.json()], max_results)

    def _search_by_package(
        self, client: httpx.Client, query: str, max_results: int
    ) -> list[SearchResult]:
        ecosystem, name = self._parse_ecosystem(query)
        resp = client.post(
            f"{OSV_API_BASE}/query",
            json={"package": {"name": name, "ecosystem": ecosystem}},
        )
        resp.raise_for_status()
        vulns = resp.json().get("vulns", [])
        return self._parse_results(vulns, max_results)

    async def _asearch_by_id(
        self, client: httpx.AsyncClient, vuln_id: str, max_results: int
    ) -> list[SearchResult]:
        resp = await client.get(f"{OSV_API_BASE}/vulns/{vuln_id}")
        resp.raise_for_status()
        return self._parse_results([resp.json()], max_results)

    async def _asearch_by_package(
        self, client: httpx.AsyncClient, query: str, max_results: int
    ) -> list[SearchResult]:
        ecosystem, name = self._parse_ecosystem(query)
        resp = await client.post(
            f"{OSV_API_BASE}/query",
            json={"package": {"name": name, "ecosystem": ecosystem}},
        )
        resp.raise_for_status()
        vulns = resp.json().get("vulns", [])
        return self._parse_results(vulns, max_results)
