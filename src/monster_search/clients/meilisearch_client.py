"""Meilisearch result cache and search client."""

from __future__ import annotations

import hashlib
import time

import httpx

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult

INDEX_NAME = "search_results"


class MeilisearchClient:
    """Client for Meilisearch — caches and re-searches previous results."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._url = self._config.meilisearch_url
        self._key = self._config.meilisearch_key
        self._timeout = self._config.meilisearch_timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}

    @staticmethod
    def _doc_id(url: str) -> str:
        """Deterministic document ID from URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _to_documents(self, query: str, results: list[SearchResult], engine: str) -> list[dict]:
        docs = []
        for r in results:
            docs.append({
                "id": self._doc_id(r.url),
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "source": r.source,
                "engine": engine,
                "query": query,
                "indexed_at": int(time.time()),
            })
        return docs

    def _to_results(self, hits: list[dict], max_results: int) -> list[SearchResult]:
        results = []
        for hit in hits[:max_results]:
            results.append(
                SearchResult(
                    title=hit.get("title", ""),
                    url=hit.get("url", ""),
                    snippet=hit.get("snippet", ""),
                    source="meilisearch",
                )
            )
        return results

    def _ensure_index(self, client: httpx.Client) -> None:
        """Create index if it doesn't exist (sync)."""
        resp = client.post(
            f"{self._url}/indexes",
            headers=self._headers(),
            json={"uid": INDEX_NAME, "primaryKey": "id"},
        )
        # 202 = created task, 409 = already exists — both fine
        if resp.status_code not in (200, 202, 409):
            resp.raise_for_status()

    async def _aensure_index(self, client: httpx.AsyncClient) -> None:
        """Create index if it doesn't exist (async)."""
        resp = await client.post(
            f"{self._url}/indexes",
            headers=self._headers(),
            json={"uid": INDEX_NAME, "primaryKey": "id"},
        )
        if resp.status_code not in (200, 202, 409):
            resp.raise_for_status()

    def index_results(
        self, query: str, results: list[SearchResult], engine: str = "unknown"
    ) -> None:
        """Index search results into Meilisearch for caching."""
        if not results:
            return
        docs = self._to_documents(query, results, engine)
        client = get_client(self._url, self._timeout)
        self._ensure_index(client)
        resp = client.post(
            f"{self._url}/indexes/{INDEX_NAME}/documents",
            headers=self._headers(),
            json=docs,
        )
        if resp.status_code not in (200, 202):
            resp.raise_for_status()

    async def aindex_results(
        self, query: str, results: list[SearchResult], engine: str = "unknown"
    ) -> None:
        """Async index search results into Meilisearch."""
        if not results:
            return
        docs = self._to_documents(query, results, engine)
        client = get_async_client(self._url, self._timeout)
        await self._aensure_index(client)
        resp = await client.post(
            f"{self._url}/indexes/{INDEX_NAME}/documents",
            headers=self._headers(),
            json=docs,
        )
        if resp.status_code not in (200, 202):
            resp.raise_for_status()

    def search(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Search cached results using Meilisearch."""
        max_results = max_results or self._config.max_results
        client = get_client(self._url, self._timeout)
        resp = client.post(
            f"{self._url}/indexes/{INDEX_NAME}/search",
            headers=self._headers(),
            json={"q": query, "limit": max_results},
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        return self._to_results(hits, max_results)

    async def asearch(self, query: str, *, max_results: int | None = None) -> list[SearchResult]:
        """Async search cached results."""
        max_results = max_results or self._config.max_results
        client = get_async_client(self._url, self._timeout)
        resp = await client.post(
            f"{self._url}/indexes/{INDEX_NAME}/search",
            headers=self._headers(),
            json={"q": query, "limit": max_results},
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        return self._to_results(hits, max_results)

    def health(self) -> bool:
        """Check if Meilisearch is reachable."""
        try:
            client = get_client(self._url, self._timeout)
            resp = client.get(f"{self._url}/health")
            return resp.status_code == 200 and resp.json().get("status") == "available"
        except (httpx.HTTPError, Exception):
            return False

    async def ahealth(self) -> bool:
        """Async health check."""
        try:
            client = get_async_client(self._url, self._timeout)
            resp = await client.get(f"{self._url}/health")
            return resp.status_code == 200 and resp.json().get("status") == "available"
        except (httpx.HTTPError, Exception):
            return False
