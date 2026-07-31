"""GitHub Repos discovery search client — runs `gh search repos` CLI."""

from __future__ import annotations

import asyncio
import json
import subprocess

from monster_search.config import Config
from monster_search.models import SearchResult


class GithubReposClient:
    """Discover GitHub repositories via gh search repos CLI."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _build_command(query: str, max_results: int) -> list[str]:
        # gh search repos treats each positional arg as a separate search token.
        # Passing a multi-word string as a single arg performs phrase matching,
        # which returns zero results for most queries. Split into individual tokens.
        query_tokens = query.split()
        return [
            "gh", "search", "repos", *query_tokens,
            "--json", "fullName,description,stargazersCount,url,language,updatedAt",
            "--limit", str(max_results),
        ]

    @staticmethod
    def _parse_results(items: list[dict], max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in items[:max_results]:
            full_name = item.get("fullName", "")
            description = item.get("description", "")
            stars = item.get("stargazersCount", 0)
            url = item.get("url", "")
            language = item.get("language", "")
            updated = item.get("updatedAt", "")

            snippet_parts = []
            if description:
                snippet_parts.append(description)
            info = []
            if stars:
                info.append(f"{stars:,} stars")
            if language:
                info.append(language)
            if info:
                snippet_parts.append(" | ".join(info))
            snippet = "\n".join(snippet_parts)

            published = updated[:10] if updated else None  # "2026-03-15T..." -> "2026-03-15"

            results.append(SearchResult(
                title=full_name,
                url=url,
                snippet=snippet,
                source="github_repos",
                published=published,
                category=language or None,
            ))
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search via gh search repos CLI."""
        max_results = max_results or self._config.max_results
        timeout = self._config.github_repos_timeout
        cmd = self._build_command(query, max_results)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"gh search repos timed out after {timeout}s") from exc
        if result.returncode != 0:
            raise RuntimeError(f"gh search repos failed (exit {result.returncode}): {result.stderr[:500]}")
        items = json.loads(result.stdout) if result.stdout.strip() else []
        return self._parse_results(items, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search via gh search repos CLI."""
        max_results = max_results or self._config.max_results
        timeout = self._config.github_repos_timeout
        cmd = self._build_command(query, max_results)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"gh search repos timed out after {timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"gh search repos failed (exit {proc.returncode}): {stderr.decode(errors='replace')[:500]}")
        items = json.loads(stdout.decode(errors='replace')) if stdout.strip() else []
        return self._parse_results(items, max_results)
