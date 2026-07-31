"""HuggingFace Hub model search client."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

_BASE_URL = "https://huggingface.co"


class HuggingFaceClient:
    """Search HuggingFace Hub models."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    @staticmethod
    def _parse_results(items: list[dict], max_results: int) -> list[SearchResult]:
        """Parse HuggingFace API response (JSON array) into SearchResults."""
        results: list[SearchResult] = []
        for item in items[:max_results]:
            model_id = item.get("modelId") or item.get("id", "")
            pipeline_tag = item.get("pipeline_tag", "")
            tags = item.get("tags", [])
            downloads = item.get("downloads", 0)
            likes = item.get("likes", 0)
            snippet_parts = []
            if pipeline_tag:
                snippet_parts.append(pipeline_tag)
            if tags:
                snippet_parts.append(", ".join(str(t) for t in tags[:5]))
            snippet_parts.append(f"{downloads:,} downloads, {likes:,} likes")
            results.append(SearchResult(
                title=model_id,
                url=f"https://huggingface.co/{model_id}",
                snippet=" | ".join(snippet_parts),
                source="huggingface",
            ))
        return results

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Synchronous search via HuggingFace Hub API."""
        max_results = max_results or self._config.max_results
        timeout = self._config.huggingface_timeout
        client = get_client(_BASE_URL, timeout)
        resp = client.get(
            f"{_BASE_URL}/api/models",
            params={"search": query, "limit": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search via HuggingFace Hub API."""
        max_results = max_results or self._config.max_results
        timeout = self._config.huggingface_timeout
        client = get_async_client(_BASE_URL, timeout)
        resp = await client.get(
            f"{_BASE_URL}/api/models",
            params={"search": query, "limit": max_results},
        )
        resp.raise_for_status()
        return self._parse_results(resp.json(), max_results)
