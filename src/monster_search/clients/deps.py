"""deps.dev package information client."""

from __future__ import annotations

from urllib.parse import quote


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

DEPS_API_BASE = "https://api.deps.dev/v3"

ECOSYSTEM_MAP = {
    "npm": "NPM",
    "pypi": "PYPI",
    "cargo": "CARGO",
    "go": "GO",
    "maven": "MAVEN",
    "nuget": "NUGET",
    "rubygems": "RUBYGEMS",
}


class DepsClient:
    """Client for deps.dev open-source package information."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_ecosystem(self, query: str) -> tuple[str, str]:
        """Parse 'ecosystem:package' format. Bare name defaults to PYPI."""
        if ":" in query:
            eco, name = query.split(":", 1)
            system = ECOSYSTEM_MAP.get(eco.lower(), eco.upper())
            return system, name
        return "PYPI", query

    def _find_default_version(self, versions: list[dict]) -> str:
        """Find the default version, or fall back to the last one."""
        for v in versions:
            if v.get("isDefault"):
                return v["versionKey"]["version"]
        if versions:
            return versions[-1]["versionKey"]["version"]
        return ""

    def _build_result(
        self, system: str, name: str, version: str, version_data: dict, versions_count: int
    ) -> SearchResult:
        """Build a SearchResult from combined package + version data."""
        licenses = ", ".join(version_data.get("licenses", [])) or "Unknown"
        advisories = version_data.get("advisoryKeys", [])
        links = version_data.get("links", [])
        published = version_data.get("publishedAt", "")

        # Find source repo link
        url = ""
        for link in links:
            if link.get("label") == "SOURCE_REPO":
                url = link.get("url", "")
                break
        if not url:
            url = f"https://deps.dev/s/{system.lower()}/p/{quote(name, safe='')}"

        snippet = (
            f"License: {licenses}. "
            f"{len(advisories)} advisory(ies). "
            f"{versions_count} version(s)."
        )

        return SearchResult(
            title=f"{name}@{version} ({system})",
            url=url,
            snippet=snippet,
            source="deps",
            published=published or None,
        )

    def _parse_results(
        self, system: str, name: str, pkg_data: dict, version_data: dict
    ) -> list[SearchResult]:
        versions = pkg_data.get("versions", [])
        version = self._find_default_version(versions)
        result = self._build_result(system, name, version, version_data, len(versions))
        return [result]

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous package lookup via deps.dev API."""
        system, name = self._parse_ecosystem(query)
        encoded_name = quote(name, safe="")
        client = get_client(DEPS_API_BASE, self._config.deps_timeout)
        # Step 1: Get package info (versions list)
        pkg_resp = client.get(
            f"{DEPS_API_BASE}/systems/{system}/packages/{encoded_name}"
        )
        pkg_resp.raise_for_status()
        pkg_data = pkg_resp.json()

        # Step 2: Get default version details
        version = self._find_default_version(pkg_data.get("versions", []))
        encoded_version = quote(version, safe="")
        ver_resp = client.get(
            f"{DEPS_API_BASE}/systems/{system}/packages/{encoded_name}/versions/{encoded_version}"
        )
        ver_resp.raise_for_status()
        return self._parse_results(system, name, pkg_data, ver_resp.json())

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async package lookup via deps.dev API."""
        system, name = self._parse_ecosystem(query)
        encoded_name = quote(name, safe="")
        client = get_async_client(DEPS_API_BASE, self._config.deps_timeout)
        pkg_resp = await client.get(
            f"{DEPS_API_BASE}/systems/{system}/packages/{encoded_name}"
        )
        pkg_resp.raise_for_status()
        pkg_data = pkg_resp.json()

        version = self._find_default_version(pkg_data.get("versions", []))
        encoded_version = quote(version, safe="")
        ver_resp = await client.get(
            f"{DEPS_API_BASE}/systems/{system}/packages/{encoded_name}/versions/{encoded_version}"
        )
        ver_resp.raise_for_status()
        return self._parse_results(system, name, pkg_data, ver_resp.json())
