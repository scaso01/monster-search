"""searchcode.com per-repository code search client.

API endpoint: POST https://api.searchcode.com/api/v1/code_search?client=monster-search
Required body fields:
  - repository: full git URL of the repo to search within
  - query:      free-text code search terms

The response nests matches inside results[].matches[], each match having:
  - line   (int)  — line number
  - line_content  — snippet of matching source
The parent result carries:
  - location  — file path within the repo
  - md5hash / sha1hash — commit-level hashes (used to build blob URLs)
  - language
  - repo      — short repo name
  - url       — direct link to file on searchcode.com

This client is explicit opt-in only — it is NOT included in --engine all
or any default tier.  Requires --repo <git-url> on the CLI.
"""

from __future__ import annotations


import httpx

from monster_search.config import Config
from monster_search.models import SearchResult

_API_URL = "https://api.searchcode.com/api/v1/code_search"
_CLIENT_TAG = "monster-search"


def _build_github_blob_url(repo_url: str, file_path: str, sha: str) -> str:
    """Construct a GitHub blob URL from repo URL + file path + commit SHA.

    Only applied when repo_url is a github.com URL; otherwise returns the
    searchcode.com file URL passed as fallback.
    """
    if "github.com" not in repo_url:
        return ""
    # Normalise: strip trailing .git if present
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    commit_ref = sha if sha else "HEAD"
    # file_path from searchcode typically starts with a leading slash
    path = file_path.lstrip("/")
    return f"{base}/blob/{commit_ref}/{path}"


def _parse_results(data: dict, max_results: int, repo_url: str) -> list[SearchResult]:
    """Flatten the nested results[].matches[] structure into SearchResult objects.

    The searchcode.com response carries:
      - results[].file       — file path within the repo
      - results[].language   — detected language
      - results[].matches[]  — line-level hits, each with `line` (int) and
                                `content` (str). Older docs reference
                                `location`/`line_content` — those keys are
                                aliased here for forward compatibility.
    No commit SHA is returned, so blob URLs default to HEAD.
    """
    out: list[SearchResult] = []
    for file_result in data.get("results", []):
        file_path = file_result.get("file") or file_result.get("location", "")
        sc_url = file_result.get("url", "")
        sha = file_result.get("md5hash", "") or file_result.get("sha1hash", "")
        language = file_result.get("language", "")

        blob_url = _build_github_blob_url(repo_url, file_path, sha) or sc_url

        for match in file_result.get("matches", []):
            if len(out) >= max_results:
                break
            line_num = match.get("line", "")
            snippet = (match.get("content") or match.get("line_content") or "").strip()
            title = file_path or "(unknown)"
            if line_num:
                title = f"{title}:{line_num}"
            if language:
                title = f"[{language}] {title}"
            out.append(SearchResult(
                title=title,
                url=f"{blob_url}#L{line_num}" if blob_url and line_num else blob_url or sc_url,
                snippet=snippet,
                source="searchcode_repo",
            ))
        if len(out) >= max_results:
            break

    return out[:max_results]


class SearchcodeRepoClient:
    """Search code within a specific repository via searchcode.com API."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def search(
        self,
        query: str,
        *,
        repository: str,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous per-repo code search.

        Args:
            query:      Code search terms.
            repository: Full git URL of the repository to search within.
            max_results: Maximum number of line-match results to return.
        """
        max_results = max_results or self._config.max_results
        timeout = self._config.searchcode_timeout

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(
                _API_URL,
                params={"client": _CLIENT_TAG},
                json={"repository": repository, "query": query},
            )
        resp.raise_for_status()
        return _parse_results(resp.json(), max_results, repository)

    async def asearch(
        self,
        query: str,
        *,
        repository: str,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async per-repo code search."""
        max_results = max_results or self._config.max_results
        timeout = self._config.searchcode_timeout

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(
                _API_URL,
                params={"client": _CLIENT_TAG},
                json={"repository": repository, "query": query},
            )
        resp.raise_for_status()
        return _parse_results(resp.json(), max_results, repository)
