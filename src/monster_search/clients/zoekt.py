"""Zoekt code search client."""

from __future__ import annotations

import base64


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class ZoektClient:
    """Client for Zoekt self-hosted code search."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _parse_results(
        self, data: dict, max_results: int
    ) -> list[SearchResult]:
        # Zoekt webserver wraps the response in {"Result": {...}}
        result = data.get("Result", data)
        repo_urls: dict[str, str] = result.get("RepoURLs") or {}
        results: list[SearchResult] = []

        for file_entry in (result.get("Files") or [])[:max_results]:
            file_name = file_entry.get("FileName", "")
            repo = file_entry.get("Repository", "")
            version = file_entry.get("Version", "")
            language = file_entry.get("Language", "")
            score = file_entry.get("Score", 0.0)

            # Build URL.
            # Zoekt's RepoURLs values are Go template strings (e.g.
            # `{{URLJoinPath "https://github.com/foo/bar" "blob" .Version .Path}}`)
            # which are unusable as-is. The Repository field carries a clean
            # `host/owner/name` form, so build a github-style /blob/ URL from
            # it directly. Fall back to repo_urls only if it looks like a
            # rendered URL (no `{{`).
            tmpl = repo_urls.get(repo, "")
            if repo.startswith("github.com/") and version and file_name:
                url = f"https://{repo}/blob/{version}/{file_name}"
            elif tmpl and "{{" not in tmpl:
                url = f"{tmpl}/blob/{version}/{file_name}" if version else tmpl
            elif repo:
                url = f"https://{repo}" if not repo.startswith("http") else repo
            else:
                url = ""

            # Decode and format line matches
            lines: list[str] = []
            for match in file_entry.get("LineMatches", [])[:3]:
                raw = match.get("Line", "")
                line_number = match.get("LineNumber", 0)
                decoded = base64.b64decode(raw).decode("utf-8", errors="replace")
                lines.append(f"L{line_number}: {decoded}")

            snippet = "\n".join(lines)

            results.append(
                SearchResult(
                    title=file_name,
                    url=url,
                    snippet=snippet,
                    source="zoekt",
                    score=score,
                    category=language,
                )
            )

        return results

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous code search via Zoekt API."""
        max_results = max_results or self._config.max_results
        body = {
            "Q": query,
            "Opts": {
                "MaxDocDisplayCount": max_results,
                "NumContextLines": 1,
            },
        }
        client = get_client(self._config.zoekt_url, self._config.zoekt_timeout)
        resp = client.post(
            f"{self._config.zoekt_url}/api/search",
            json=body,
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async code search via Zoekt API."""
        max_results = max_results or self._config.max_results
        body = {
            "Q": query,
            "Opts": {
                "MaxDocDisplayCount": max_results,
                "NumContextLines": 1,
            },
        }
        client = get_async_client(self._config.zoekt_url, self._config.zoekt_timeout)
        resp = await client.post(
            f"{self._config.zoekt_url}/api/search",
            json=body,
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
