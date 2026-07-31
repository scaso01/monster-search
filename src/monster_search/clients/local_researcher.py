"""Local Deep Researcher client using LangGraph REST API."""

from __future__ import annotations

import asyncio
import re

import httpx

from monster_search.config import Config
from monster_search.models import SearchResult

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_BULLET_LINK_RE = re.compile(r"\*\s+(.+?)\s*:\s*(https?://\S+)")

_ASSISTANT_GRAPH = "ollama_deep_researcher"


def _extract_sources(
    report: str, sources_gathered: list[str] | None = None
) -> list[SearchResult]:
    """Extract sources from report text and/or the sources_gathered state field.

    Tries markdown links ``[title](url)`` first, then falls back to bullet-point
    format ``* Title : https://...`` which is what the LDR LLM actually produces.
    The ``sources_gathered`` list (from LangGraph state) uses the bullet-point
    format and is the most reliable source of URLs.
    """
    seen: set[str] = set()
    results: list[SearchResult] = []

    def _add(title: str, url: str) -> None:
        cleaned = url.rstrip(".,;:!?)")
        if cleaned in seen:
            return
        seen.add(cleaned)
        results.append(
            SearchResult(
                title=title.strip(), url=cleaned, snippet="", source="local_researcher"
            )
        )

    # 1. Structured sources_gathered (most reliable)
    if sources_gathered:
        for entry in sources_gathered:
            for title, url in _BULLET_LINK_RE.findall(entry):
                _add(title, url)

    # 2. Markdown links in the report text
    for title, url in _MD_LINK_RE.findall(report):
        _add(title, url)

    # 3. Bullet-point links in the report text (fallback)
    for title, url in _BULLET_LINK_RE.findall(report):
        _add(title, url)

    return results


class LocalResearcherClient:
    """Client for LangChain Local Deep Researcher."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    async def asearch(self, query: str) -> tuple[str, list[SearchResult]]:
        """Async research via LangGraph REST API."""
        base = self._config.local_researcher_url
        timeout = httpx.Timeout(self._config.local_researcher_timeout)

        async with httpx.AsyncClient(base_url=base, timeout=timeout) as client:
            # 1. Create thread
            resp = await client.post("/threads", json={})
            resp.raise_for_status()
            thread_id = resp.json()["thread_id"]

            # 2. Search for the assistant by graph_id
            resp = await client.post(
                "/assistants/search",
                json={"graph_id": _ASSISTANT_GRAPH},
            )
            resp.raise_for_status()
            assistants = resp.json()
            if not assistants:
                raise RuntimeError(f"No assistant found for graph '{_ASSISTANT_GRAPH}'")
            assistant_id = assistants[0]["assistant_id"]

            # 3. Start run and wait for completion
            resp = await client.post(
                f"/threads/{thread_id}/runs/wait",
                json={
                    "assistant_id": assistant_id,
                    "input": {"research_topic": query},
                },
            )
            resp.raise_for_status()

            # 4. Get final state
            resp = await client.get(f"/threads/{thread_id}/state")
            resp.raise_for_status()
            state = resp.json()

            values = state.get("values") or {}
            report = values.get("running_summary", "")
            sources_gathered = values.get("sources_gathered") or []
            return report, _extract_sources(report, sources_gathered)

    def search(self, query: str) -> tuple[str, list[SearchResult]]:
        """Synchronous research via Local Deep Researcher."""
        return asyncio.run(self.asearch(query))
