"""Data models for monster-search."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Normalized search result from any source."""

    title: str
    url: str
    snippet: str
    source: str  # "searxng" | "perplexica" | "marginalia" | "perplexity" | "local_researcher" | "crawl4ai" | "fused" | "all"
    engine: str | None = None
    score: float | None = None
    published: str | None = None
    category: str | None = None
    sources: tuple[str, ...] | None = None
    fused_score: float | None = None
    price: str | None = None
    in_stock: bool | None = None

    def brief(self) -> str:
        """Format as compact CLI-friendly string."""
        truncated = self.snippet[:200] + "..." if len(self.snippet) > 200 else self.snippet
        sources_line = f"\n    [Found by: {', '.join(self.sources)}]" if self.sources else ""
        price_line = f"\n    Price: {self.price}" if self.price else ""
        return f"{self.title}\n    {self.url}\n    {truncated}{sources_line}{price_line}"
