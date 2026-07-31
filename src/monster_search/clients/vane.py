"""Vane AI-powered search client (Perplexica fork)."""

from __future__ import annotations

from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class VaneClient:
    """Client for Vane AI search (Perplexica-compatible API)."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._chat_provider_id: str | None = None
        self._embed_provider_id: str | None = None
        self._model_key: str = ""

    def _resolve_provider_ids_from_data(self, providers: list[dict]) -> tuple[str, str, str]:
        """Extract chat provider ID, embedding provider ID, and model key."""
        chat_id = None
        model_key = ""
        embed_id = None
        for p in providers:
            if p.get("chatModels") and not chat_id:
                chat_id = p["id"]
                model_key = p["chatModels"][0].get("key", p["chatModels"][0].get("name", ""))
            if p.get("embeddingModels") and not embed_id:
                embed_id = p["id"]
        if not chat_id or not embed_id:
            raise RuntimeError("Could not resolve Vane provider IDs")
        return chat_id, embed_id, model_key

    def _resolve_provider_ids(self) -> tuple[str, str]:
        """Fetch provider UUIDs from Vane API (they change on container rebuild)."""
        if self._chat_provider_id and self._embed_provider_id:
            return self._chat_provider_id, self._embed_provider_id

        client = get_client(self._config.vane_url, self._config.timeout)
        resp = client.get(f"{self._config.vane_url}/api/providers")
        resp.raise_for_status()
        providers = resp.json()["providers"]

        chat_id, embed_id, model_key = self._resolve_provider_ids_from_data(providers)
        self._chat_provider_id = chat_id
        self._embed_provider_id = embed_id
        self._model_key = model_key
        return chat_id, embed_id

    async def _aresolve_provider_ids(self) -> tuple[str, str]:
        """Async version of provider ID resolution."""
        if self._chat_provider_id and self._embed_provider_id:
            return self._chat_provider_id, self._embed_provider_id

        client = get_async_client(self._config.vane_url, self._config.timeout)
        resp = await client.get(f"{self._config.vane_url}/api/providers")
        resp.raise_for_status()
        providers = resp.json()["providers"]

        chat_id, embed_id, model_key = self._resolve_provider_ids_from_data(providers)
        self._chat_provider_id = chat_id
        self._embed_provider_id = embed_id
        self._model_key = model_key
        return chat_id, embed_id

    def _build_payload(self, query: str, chat_id: str, embed_id: str, focus_mode: str) -> dict:
        """Build the search request payload."""
        return {
            "chatModel": {"providerId": chat_id, "key": self._model_key},
            "embeddingModel": {"providerId": embed_id, "key": "Xenova/all-MiniLM-L6-v2"},
            "focusMode": focus_mode,
            "optimizationMode": "speed",
            "query": query,
            "history": [],
            "sources": ["web"],
        }

    def _parse_results(self, data: dict) -> tuple[str, list[SearchResult]]:
        """Parse Vane API response into SearchResult list."""
        message = data.get("message", "")
        results = []
        for source in data.get("sources", []):
            meta = source.get("metadata", source)
            results.append(
                SearchResult(
                    title=meta.get("title", ""),
                    url=meta.get("url", ""),
                    snippet=source.get("content", ""),
                    source="vane",
                )
            )
        return message, results

    def search(
        self,
        query: str,
        *,
        focus_mode: str = "webSearch",
    ) -> tuple[str, list[SearchResult]]:
        """Synchronous AI-powered search via Vane."""
        chat_id, embed_id = self._resolve_provider_ids()
        payload = self._build_payload(query, chat_id, embed_id, focus_mode)
        client = get_client(self._config.vane_url, self._config.vane_timeout)
        resp = client.post(f"{self._config.vane_url}/api/search", json=payload)
        resp.raise_for_status()
        return self._parse_results(resp.json())

    async def asearch(
        self,
        query: str,
        *,
        focus_mode: str = "webSearch",
    ) -> tuple[str, list[SearchResult]]:
        """Async AI-powered search via Vane."""
        chat_id, embed_id = await self._aresolve_provider_ids()
        payload = self._build_payload(query, chat_id, embed_id, focus_mode)
        client = get_async_client(self._config.vane_url, self._config.vane_timeout)
        resp = await client.post(f"{self._config.vane_url}/api/search", json=payload)
        resp.raise_for_status()
        return self._parse_results(resp.json())
