"""Perplexica AI-powered search client."""

from __future__ import annotations


from monster_search.clients._pool import get_async_client, get_client
from monster_search.config import Config
from monster_search.models import SearchResult


class PerplexicaClient:
    """Client for Perplexica AI search."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()
        self._chat_provider_id: str | None = None
        self._embed_provider_id: str | None = None
        self._resolved_model_key: str | None = None
        self._model_field: str = "key"  # "key" for OpenAI, "name" for llama-server

    @staticmethod
    def _model_id(model: dict) -> tuple[str, str]:
        """Extract (field_name, value) — 'key' (OpenAI-style) or 'name' (llama-server)."""
        if "key" in model:
            return "key", model["key"]
        return "name", model.get("name", "")

    def _match_providers(self, providers: list[dict], target_model: str) -> tuple[str, str, str | None, str]:
        """Find chat and embedding provider IDs matching target_model."""
        chat_id = None
        matched_key: str | None = None
        model_field = "key"
        embed_id = None
        for p in providers:
            if p.get("chatModels") and not chat_id:
                for m in p["chatModels"]:
                    field, value = self._model_id(m)
                    if value == target_model:
                        chat_id = p["id"]
                        matched_key = value
                        model_field = field
                        break
            if p.get("embeddingModels") and not embed_id:
                embed_id = p["id"]

        if not chat_id:
            for p in providers:
                if p.get("chatModels"):
                    chat_id = p["id"]
                    field, value = self._model_id(p["chatModels"][0])
                    matched_key = value
                    model_field = field
                    break
        if not chat_id or not embed_id:
            raise RuntimeError("Could not resolve Perplexica provider IDs")
        return chat_id, embed_id, matched_key, model_field

    def _resolve_provider_ids(self, *, model_override: str | None = None) -> tuple[str, str]:
        """Fetch provider UUIDs from Perplexica API (they change on container rebuild)."""
        if not model_override and self._chat_provider_id and self._embed_provider_id:
            return self._chat_provider_id, self._embed_provider_id

        client = get_client(self._config.perplexica_url, self._config.timeout)
        resp = client.get(f"{self._config.perplexica_url}/api/providers")
        resp.raise_for_status()
        providers = resp.json()["providers"]

        target_model = model_override or self._config.perplexica_model
        chat_id, embed_id, matched_key, model_field = self._match_providers(providers, target_model)

        if not model_override:
            self._chat_provider_id = chat_id
            self._embed_provider_id = embed_id
        self._resolved_model_key = matched_key
        self._model_field = model_field
        return chat_id, embed_id

    async def _aresolve_provider_ids(self, *, model_override: str | None = None) -> tuple[str, str]:
        """Async version of provider ID resolution."""
        if not model_override and self._chat_provider_id and self._embed_provider_id:
            return self._chat_provider_id, self._embed_provider_id

        client = get_async_client(self._config.perplexica_url, self._config.timeout)
        resp = await client.get(f"{self._config.perplexica_url}/api/providers")
        resp.raise_for_status()
        providers = resp.json()["providers"]

        target_model = model_override or self._config.perplexica_model
        chat_id, embed_id, matched_key, model_field = self._match_providers(providers, target_model)

        if not model_override:
            self._chat_provider_id = chat_id
            self._embed_provider_id = embed_id
        self._resolved_model_key = matched_key
        self._model_field = model_field
        return chat_id, embed_id

    def _build_payload(self, query: str, chat_id: str, embed_id: str, focus_mode: str) -> dict:
        model_value = self._resolved_model_key or self._config.perplexica_model
        return {
            "chatModel": {"providerId": chat_id, self._model_field: model_value},
            "embeddingModel": {"providerId": embed_id, "key": "Xenova/all-MiniLM-L6-v2"},
            "focusMode": focus_mode,
            "optimizationMode": "speed",
            "query": query,
            "history": [],
            "sources": ["web"],
        }

    def _parse_results(self, data: dict) -> tuple[str, list[SearchResult]]:
        message = data.get("message", "")
        results = []
        for source in data.get("sources", []):
            # Sources may have title/url at the top level (older API) or
            # nested under a "metadata" key (current Perplexica builds).
            meta = source.get("metadata", source)
            results.append(
                SearchResult(
                    title=meta.get("title", ""),
                    url=meta.get("url", ""),
                    snippet=source.get("content", ""),
                    source="perplexica",
                )
            )
        return message, results

    def search(
        self,
        query: str,
        *,
        focus_mode: str = "webSearch",
        model: str | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Synchronous AI-powered search via Perplexica."""
        chat_id, embed_id = self._resolve_provider_ids(model_override=model)
        payload = self._build_payload(query, chat_id, embed_id, focus_mode)
        client = get_client(self._config.perplexica_url, self._config.perplexica_timeout)
        resp = client.post(f"{self._config.perplexica_url}/api/search", json=payload)
        resp.raise_for_status()
        return self._parse_results(resp.json())

    async def asearch(
        self,
        query: str,
        *,
        focus_mode: str = "webSearch",
        model: str | None = None,
    ) -> tuple[str, list[SearchResult]]:
        """Async AI-powered search via Perplexica."""
        chat_id, embed_id = await self._aresolve_provider_ids(model_override=model)
        payload = self._build_payload(query, chat_id, embed_id, focus_mode)
        client = get_async_client(self._config.perplexica_url, self._config.perplexica_timeout)
        resp = await client.post(f"{self._config.perplexica_url}/api/search", json=payload)
        resp.raise_for_status()
        return self._parse_results(resp.json())
