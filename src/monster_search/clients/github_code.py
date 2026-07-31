"""GitHub Code Search client — runs `gh search code` CLI."""

from __future__ import annotations

import asyncio
import json
import subprocess

from monster_search.config import Config
from monster_search.models import SearchResult


class GithubCodeClient:
    """Search public code via GitHub CLI (gh search code)."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _build_command(query: str, max_results: int) -> list[str]:
        return [
            "gh", "search", "code", query,
            "--json", "path,repository,textMatches",
            "--limit", str(max_results),
        ]

    @staticmethod
    def _parse_results(items: list[dict], max_results: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in items[:max_results]:
            repo = item.get("repository", {})
            repo_name = repo.get("nameWithOwner") or repo.get("fullName", "")
            path = item.get("path", "")
            text_matches = item.get("textMatches", [])
            snippet = "\n".join(m.get("fragment", "") for m in text_matches)
            url = f"https://github.com/{repo_name}/blob/HEAD/{path}" if repo_name and path else ""
            results.append(SearchResult(
                title=f"{repo_name}/{path}" if repo_name else path,
                url=url,
                snippet=snippet,
                source="github_code",
            ))
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search via gh search code CLI."""
        max_results = max_results or self._config.max_results
        timeout = self._config.github_code_timeout
        cmd = self._build_command(query, max_results)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"gh search code timed out after {timeout}s") from exc
        if result.returncode != 0:
            raise RuntimeError(f"gh search code failed (exit {result.returncode}): {result.stderr[:500]}")
        items = json.loads(result.stdout) if result.stdout.strip() else []
        return self._parse_results(items, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search via gh search code CLI."""
        max_results = max_results or self._config.max_results
        timeout = self._config.github_code_timeout
        cmd = self._build_command(query, max_results)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"gh search code timed out after {timeout}s")
        if proc.returncode != 0:
            raise RuntimeError(f"gh search code failed (exit {proc.returncode}): {stderr.decode()[:500]}")
        items = json.loads(stdout.decode()) if stdout.strip() else []
        return self._parse_results(items, max_results)
