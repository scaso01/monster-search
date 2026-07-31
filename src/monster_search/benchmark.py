"""Benchmark runner for monster-search engines."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from monster_search.config import Config
from monster_search.models import SearchResult

# Engines that accept text queries (exclude crawl which takes URLs).
# semantic_scholar is omitted when no MONSTER_SEMANTIC_SCHOLAR_API_KEY is set —
# running it without a key triggers 429s and skews the benchmark table.
_BASE_BENCHMARKABLE_ENGINES = [
    "searxng", "marginalia", "mwmbl", "news", "gnews",
    "arxiv", "openalex",
    "osv", "deps", "whodat", "zoekt",
    "archive_org", "perplexity", "synthesizer", "local_researcher",
    "vane", "khoj", "fyin",
    "youtube", "github_code", "github_repos",
    "hackernews", "huggingface", "reddit",
    "cheapshark", "slickdeals",
]

_grepapp_enabled = os.environ.get("MONSTER_GREPAPP_ENABLED", "").lower() in ("1", "true", "yes")
_ss_enabled = bool(os.environ.get("MONSTER_SEMANTIC_SCHOLAR_API_KEY", ""))

BENCHMARKABLE_ENGINES = (
    (["semantic_scholar"] if _ss_enabled else [])
    + (["grepapp"] if _grepapp_enabled else [])
    + _BASE_BENCHMARKABLE_ENGINES
)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Timing result for a single engine benchmark run."""

    engine: str
    status: str  # "ok", "error", "timeout"
    result_count: int
    elapsed_seconds: float
    error: str | None = None


def _run_engine(
    engine: str, query: str, config: Config, max_results: int,
) -> tuple[str, list[SearchResult]]:
    """Run a single engine synchronously, return (status, results)."""
    if engine == "searxng":
        from monster_search.clients.searxng import SearXNGClient
        r = SearXNGClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "marginalia":
        from monster_search.clients.marginalia import MarginaliaClient
        r = MarginaliaClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "mwmbl":
        from monster_search.clients.mwmbl import MwmblClient
        r = MwmblClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "news":
        from monster_search.clients.news import NewsSearchClient
        _, r = NewsSearchClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "gnews":
        from monster_search.clients.gnews import GNewsClient
        r = GNewsClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "semantic_scholar":
        from monster_search.clients.semantic_scholar import SemanticScholarClient
        r = SemanticScholarClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "arxiv":
        from monster_search.clients.arxiv import ArxivClient
        r = ArxivClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "openalex":
        from monster_search.clients.openalex import OpenAlexClient
        r = OpenAlexClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "osv":
        from monster_search.clients.osv import OsvClient
        r = OsvClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "deps":
        from monster_search.clients.deps import DepsClient
        r = DepsClient(config=config).search(query)
        return "ok", r
    elif engine == "whodat":
        from monster_search.clients.whodat import WhoDatClient
        r = WhoDatClient(config=config).search(query)
        return "ok", r
    elif engine == "zoekt":
        from monster_search.clients.zoekt import ZoektClient
        r = ZoektClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "archive_org":
        from monster_search.clients.archive_org import ArchiveOrgClient
        r = ArchiveOrgClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "perplexity":
        from monster_search.clients.perplexity_client import PerplexityClient
        _, r = PerplexityClient(config=config).search(query)
        return "ok", r
    elif engine == "synthesizer":
        from monster_search.clients.synthesizer import SynthesizerClient
        _, r = SynthesizerClient(config=config).search(query)
        return "ok", r
    elif engine == "local_researcher":
        from monster_search.clients.local_researcher import LocalResearcherClient
        _, r = LocalResearcherClient(config=config).search(query)
        return "ok", r
    elif engine == "vane":
        from monster_search.clients.vane import VaneClient
        _, r = VaneClient(config=config).search(query)
        return "ok", r
    elif engine == "khoj":
        from monster_search.clients.khoj import KhojClient
        _, r = KhojClient(config=config).search(query)
        return "ok", r
    elif engine == "fyin":
        from monster_search.clients.fyin import FyinClient
        _, r = FyinClient(config=config).search(query)
        return "ok", r
    elif engine == "youtube":
        from monster_search.clients.youtube import YouTubeClient
        r = YouTubeClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "grepapp":
        from monster_search.clients.grepapp import GrepAppClient
        r = GrepAppClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "github_code":
        from monster_search.clients.github_code import GithubCodeClient
        r = GithubCodeClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "github_repos":
        from monster_search.clients.github_repos import GithubReposClient
        r = GithubReposClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "hackernews":
        from monster_search.clients.hackernews import HackerNewsClient
        r = HackerNewsClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "huggingface":
        from monster_search.clients.huggingface import HuggingFaceClient
        r = HuggingFaceClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "reddit":
        from monster_search.clients.reddit import RedditClient
        r = RedditClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "cheapshark":
        from monster_search.clients.cheapshark import CheapSharkClient
        r = CheapSharkClient(config=config).search(query, max_results=max_results)
        return "ok", r
    elif engine == "slickdeals":
        from monster_search.clients.slickdeals import SlickdealsClient
        r = SlickdealsClient(config=config).search(query, max_results=max_results)
        return "ok", r
    else:
        return "error", []


def run_benchmark(
    query: str,
    config: Config | None = None,
    *,
    engines: list[str] | None = None,
) -> list[BenchmarkResult]:
    """Benchmark engines sequentially for isolated timing."""
    config = config or Config()
    engines = engines or BENCHMARKABLE_ENGINES
    results: list[BenchmarkResult] = []

    for engine in engines:
        start = time.perf_counter()
        try:
            status, items = _run_engine(engine, query, config, config.max_results)
            elapsed = time.perf_counter() - start
            results.append(BenchmarkResult(
                engine=engine, status=status,
                result_count=len(items), elapsed_seconds=round(elapsed, 2),
            ))
        except TimeoutError:
            elapsed = time.perf_counter() - start
            results.append(BenchmarkResult(
                engine=engine, status="timeout",
                result_count=0, elapsed_seconds=round(elapsed, 2),
            ))
        except Exception as exc:
            elapsed = time.perf_counter() - start
            results.append(BenchmarkResult(
                engine=engine, status="error",
                result_count=0, elapsed_seconds=round(elapsed, 2),
                error=str(exc)[:80],
            ))

    return results


def format_table(results: list[BenchmarkResult]) -> str:
    """Format benchmark results as a CLI table."""
    lines = [
        f"{'Engine':<22} {'Status':<9} {'Results':>7} {'Time':>8}",
        f"{'─' * 22} {'─' * 9} {'─' * 7} {'─' * 8}",
    ]
    for r in results:
        time_str = f"{r.elapsed_seconds:.1f}s"
        error_str = f"  ({r.error})" if r.error else ""
        lines.append(f"{r.engine:<22} {r.status:<9} {r.result_count:>7} {time_str:>8}{error_str}")

    # Summary
    ok_count = sum(1 for r in results if r.status == "ok")
    total_time = sum(r.elapsed_seconds for r in results)
    lines.append(f"{'─' * 22} {'─' * 9} {'─' * 7} {'─' * 8}")
    lines.append(f"{'TOTAL':<22} {ok_count}/{len(results)} ok {'':>7} {total_time:.1f}s")

    return "\n".join(lines)
