"""All-engines composite client — runs query engines with circuit breakers, fusion, and dedup."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import re as _re

from monster_search.clients.archive_org import ArchiveOrgClient
from monster_search.clients.arxiv import ArxivClient
from monster_search.clients.deps import DepsClient
from monster_search.clients.gnews import GNewsClient
from monster_search.clients.local_researcher import LocalResearcherClient
from monster_search.clients.marginalia import MarginaliaClient
from monster_search.clients.mwmbl import MwmblClient
from monster_search.clients.meilisearch_client import MeilisearchClient
from monster_search.clients.news import NewsSearchClient
from monster_search.clients.openalex import OpenAlexClient
from monster_search.clients.osv import OsvClient
from monster_search.clients.perplexity_client import PerplexityClient
from monster_search.clients.synthesizer import SynthesizerClient
from monster_search.clients.searxng import SearXNGClient
from monster_search.clients.semantic_scholar import SemanticScholarClient
from monster_search.clients.whodat import WhoDatClient
from monster_search.clients.ddg_browser import DdgBrowserClient
from monster_search.clients.fyin import FyinClient
from monster_search.clients.khoj import KhojClient
from monster_search.clients.vane import VaneClient
from monster_search.clients.youtube import YouTubeClient
from monster_search.clients.grepapp import GrepAppClient
from monster_search.clients.github_code import GithubCodeClient
from monster_search.clients.github_repos import GithubReposClient
from monster_search.clients.hackernews import HackerNewsClient
from monster_search.clients.huggingface import HuggingFaceClient
from monster_search.clients.reddit import RedditClient
from monster_search.clients.cheapshark import CheapSharkClient
from monster_search.clients.slickdeals import SlickdealsClient
from monster_search.clients.deals_rss import DealsRSSClient
from monster_search.clients.priceghost import PriceGhostClient
from monster_search.clients.amazon_deals import AmazonDealsClient
from monster_search.clients.newegg import NeweggClient
from monster_search.clients.zoekt import ZoektClient
from monster_search._breaker import get_breaker, CircuitOpenError, failure_reason, timed_call
from monster_search._dedup import deduplicate_results
from monster_search._router import classify_query, QueryCategory
from monster_search._tiered import EventCallback, tiered_search, tier_of
from monster_search.config import Config
from monster_search.fusion import fuse_results, bm25_rerank
from monster_search.models import SearchResult

_URL_RE = _re.compile(r"^https?://", _re.IGNORECASE)

# Ceiling on the Meilisearch cache write at the end of a search. The cache is a
# nicety; a slow or unreachable Meilisearch must never extend a search.
_CACHE_WRITE_TIMEOUT_S = 5.0

# Preference order for which engine's synthesized answer leads the headline.
# Perplexity/Vane/Khoj/Fyin/Local-Researcher produce better prose than the local
# synthesizer; previously only the synthesizer's answer survived the tiered runner.
_ANSWER_PREFERENCE: tuple[str, ...] = (
    "perplexity", "vane", "khoj", "fyin", "local_researcher", "synthesizer",
)


def _select_best_answer(answers: dict[str, str]) -> str:
    """Return the best available synthesized answer text, by source preference."""
    for engine in _ANSWER_PREFERENCE:
        text = answers.get(engine)
        if text and text.strip():
            return text
    for text in answers.values():
        if text and text.strip():
            return text
    return ""


class AllEnginesClient:
    """Runs query engines with circuit breakers, RRF fusion, and content dedup.

    Always-on (tier1): SearXNG, Marginalia, News, GNews, Perplexity, Synthesizer.
    Tier2 (auto-promote when tier1 sparse): Vane, Khoj, Fyin.
    Tier3 (--deep only): Local Researcher.
    Router-gated: arXiv + Semantic Scholar + OpenAlex (ACADEMIC),
    OSV (SECURITY), deps.dev (PACKAGE), Who-Dat (WHOIS),
    Zoekt (CODE), Archive.org (ARCHIVE or URL query).
    Removed: Perplexica (container removed, Vane replaced it).
    Background only: Meilisearch (result cache).
    Excluded: Crawl4AI and changedetection.io (take URLs, not queries).
    """

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _build_engines(
        self, query: str, max_results: int, **kwargs,
    ) -> dict[str, object]:
        """Build coroutine dict for engines, using router to gate specialists.

        Always-on engines run for every query.  Specialized engines only
        run when ``classify_query`` returns a matching category (or the
        query looks like a URL for archive_org).

        Removed from result set:
        * Perplexica — replaced by Synthesizer (container removed).
        * Meilisearch — background cache only (still used in ``_cache_results``).
        """
        config = self._config
        category = classify_query(query)

        # ---- Always-on engines (work with any free-text query) ----
        engines: dict[str, object] = {
            "searxng": get_breaker("searxng").call(
                SearXNGClient(config=config).asearch(
                    query, category=kwargs.get("category"), max_results=max_results
                )
            ),
            "marginalia": get_breaker("marginalia").call(
                MarginaliaClient(config=config).asearch(query, max_results=max_results)
            ),
            "mwmbl": get_breaker("mwmbl").call(
                MwmblClient(config=config).asearch(query, max_results=max_results)
            ),
            **(
                {
                    "ddg": get_breaker("ddg").call(
                        DdgBrowserClient(config=config).asearch(query, max_results=max_results)
                    )
                }
                if config.ddg_enabled
                else {}
            ),
            "news": get_breaker("news").call(
                NewsSearchClient(config=config).asearch(query, max_results=max_results)
            ),
            "gnews": get_breaker("gnews").call(
                GNewsClient(config=config).asearch(query, max_results=max_results)
            ),
            "perplexity": get_breaker("perplexity").call(
                PerplexityClient(config=config).asearch(query)
            ),
            "synthesizer": get_breaker("synthesizer").call(
                SynthesizerClient(config=config).asearch(query)
            ),
            "youtube": get_breaker("youtube").call(
                YouTubeClient(config=config).asearch(query, max_results=max_results)
            ),
            "hackernews": get_breaker("hackernews").call(
                HackerNewsClient(config=config).asearch(query, max_results=max_results)
            ),
            "reddit": get_breaker("reddit").call(
                RedditClient(config=config).asearch(query, max_results=max_results)
            ),
            "github_repos": get_breaker("github_repos").call(
                GithubReposClient(config=config).asearch(query, max_results=max_results)
            ),
        }

        # ---- Router-gated: only add when query type matches ----
        if category == QueryCategory.ACADEMIC:
            engines["arxiv"] = get_breaker("arxiv").call(
                ArxivClient(config=config).asearch(query, max_results=max_results)
            )
            # Semantic Scholar is gated on API key presence — signup requires a
            # non-gmail address (as of 2026-04).  When no key is set the engine is
            # invisible; set MONSTER_SEMANTIC_SCHOLAR_API_KEY in .env to re-enable.
            if config.semantic_scholar_api_key:
                engines["semantic_scholar"] = get_breaker("semantic_scholar").call(
                    SemanticScholarClient(config=config).asearch(query, max_results=max_results)
                )
            engines["openalex"] = get_breaker("openalex").call(
                OpenAlexClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.SECURITY:
            engines["osv"] = get_breaker("osv").call(
                OsvClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.PACKAGE:
            engines["deps"] = get_breaker("deps").call(
                DepsClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.WHOIS:
            engines["whodat"] = get_breaker("whodat").call(
                WhoDatClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.CODE:
            engines["zoekt"] = get_breaker("zoekt").call(
                ZoektClient(config=config).asearch(query, max_results=max_results)
            )
            # grepapp is gated: it 429s whole hosting and VPN ranges outright.
            # Set MONSTER_GREPAPP_ENABLED=true in .env to re-enable without VPN.
            if config.grepapp_enabled:
                engines["grepapp"] = get_breaker("grepapp").call(
                    GrepAppClient(config=config).asearch(query, max_results=max_results)
                )
            engines["github_code"] = get_breaker("github_code").call(
                GithubCodeClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.SHOPPING:
            engines["slickdeals"] = get_breaker("slickdeals").call(
                SlickdealsClient(config=config).asearch(query, max_results=max_results)
            )
            engines["cheapshark"] = get_breaker("cheapshark").call(
                CheapSharkClient(config=config).asearch(query, max_results=max_results)
            )
            engines["deals_rss"] = get_breaker("deals_rss").call(
                DealsRSSClient(config=config).asearch(query, max_results=max_results)
            )
            engines["priceghost"] = get_breaker("priceghost").call(
                PriceGhostClient(config=config).asearch(query, max_results=max_results)
            )
            engines["amazon_deals"] = get_breaker("amazon_deals").call(
                AmazonDealsClient(config=config).asearch(query, max_results=max_results)
            )
            engines["newegg"] = get_breaker("newegg").call(
                NeweggClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.AI_ML:
            engines["huggingface"] = get_breaker("huggingface").call(
                HuggingFaceClient(config=config).asearch(query, max_results=max_results)
            )

        # Archive: gated by category OR URL-shaped query
        if category == QueryCategory.ARCHIVE or _URL_RE.search(query):
            engines["archive_org"] = get_breaker("archive_org").call(
                ArchiveOrgClient(config=config).asearch(query, max_results=max_results)
            )

        return engines

    @staticmethod
    def _collect_results(
        engines: dict[str, object],
        results_by_engine: list,
    ) -> tuple[list[str], list[str], dict[str, list[SearchResult]], list[str]]:
        """Parse gather results into succeeded/failed/engine_results/messages."""
        succeeded = []
        failed = []
        engine_results: dict[str, list[SearchResult]] = {}
        message_parts: list[str] = []

        for name, result in zip(engines.keys(), results_by_engine):
            if isinstance(result, BaseException):
                failed.append(name)
                if not isinstance(result, CircuitOpenError):
                    print(f"WARNING: {name} failed: {result}", file=sys.stderr)
                continue

            succeeded.append(name)

            if isinstance(result, tuple):
                msg, items = result
                if msg:
                    message_parts.append(msg)
                engine_results[name] = items
            elif isinstance(result, list):
                engine_results[name] = result

        return succeeded, failed, engine_results, message_parts

    async def _cache_results(
        self, query: str, engine_results: dict[str, list[SearchResult]],
    ) -> None:
        """Fire-and-forget: index results into Meilisearch cache."""
        try:
            meili = MeilisearchClient(config=self._config)
            for name, items in engine_results.items():
                if name != "meilisearch" and items:
                    await meili.aindex_results(query, items, engine=name)
        except Exception:
            pass  # Meilisearch down is non-fatal

    async def search(
        self, query: str, *, fuse: bool = True, **kwargs,
    ) -> tuple[str, list[SearchResult]]:
        """Run all engines in parallel with circuit breakers, fuse + dedup results."""
        max_results = kwargs.pop("max_results", None) or self._config.max_results
        engines = self._build_engines(query, max_results, **kwargs)

        results_by_engine = await asyncio.gather(
            *engines.values(), return_exceptions=True
        )

        succeeded, failed, engine_results, message_parts = self._collect_results(
            engines, results_by_engine
        )

        # Fuse or legacy dedup
        if fuse and len(engine_results) > 0:
            combined = fuse_results(engine_results)
            combined = deduplicate_results(combined)
        else:
            seen_urls: set[str] = set()
            combined: list[SearchResult] = []
            for items in engine_results.values():
                for r in items:
                    if r.url not in seen_urls:
                        seen_urls.add(r.url)
                        combined.append(r)

        # Cache results in background (non-blocking)
        asyncio.ensure_future(self._cache_results(query, engine_results))

        # Build summary message
        status_parts = []
        if succeeded:
            status_parts.append(f"Succeeded: {', '.join(succeeded)}")
        if failed:
            status_parts.append(f"Failed: {', '.join(failed)}")
        status_line = " | ".join(status_parts)

        if message_parts:
            message = status_line + "\n\n" + "\n\n---\n\n".join(message_parts)
        else:
            message = status_line

        return message, combined

    async def smart_search_rich(
        self, query: str, *, include_slow: bool = False, fuse: bool = True,
        on_event: EventCallback | None = None, **kwargs,
    ) -> dict[str, Any]:
        """Smart tiered search with full provenance: classify query, route to
        relevant engines only, and report what every engine produced.

        Returns a dict::

            {
              "message": str,                 # status line
              "answer": str,                  # best available synthesized answer
              "answers": {engine: text},      # every engine's answer (was discarded)
              "results": [SearchResult, ...], # fused/deduped link results
              "engine_status": {engine: {state, count, reason?}},
              "per_engine": {engine: [SearchResult, ...]},  # raw, pre-fusion
            }

        ``per_engine`` holds each engine's RAW (pre-fusion) result list so the
        dashboard can show "everything each source returned"; its lengths match
        ``engine_status[engine]["count"]`` exactly, while ``results`` is the
        fused/deduped pool (so the two counts legitimately differ).

        ``answer`` is the best available answer by source preference
        (Perplexity > Vane > Khoj > Fyin > Local Researcher > synthesizer), not
        synthesizer-only as before.  ``engine_status`` reports ok / empty /
        failed / skipped per engine for the dashboard visibility panel.
        """
        config = self._config
        max_results = kwargs.get("max_results") or config.max_results
        category = classify_query(query)

        # Build engine callables (lambdas that return coroutines) for tiered execution.
        # NOTE: "synthesizer" is intentionally omitted here; we run it separately so
        # we can capture its answer text before _tiered._run_tier discards it.
        def _make_engines() -> dict[str, object]:
            return {
                "searxng": lambda: get_breaker("searxng").call(
                    SearXNGClient(config=config).asearch(
                        query, category=kwargs.get("category"), max_results=max_results
                    )
                ),
                "marginalia": lambda: get_breaker("marginalia").call(
                    MarginaliaClient(config=config).asearch(query, max_results=max_results)
                ),
                "mwmbl": lambda: get_breaker("mwmbl").call(
                    MwmblClient(config=config).asearch(query, max_results=max_results)
                ),
                **(
                    {
                        "ddg": lambda: get_breaker("ddg").call(
                            DdgBrowserClient(config=config).asearch(
                                query, max_results=max_results
                            )
                        )
                    }
                    if config.ddg_enabled
                    else {}
                ),
                "news": lambda: get_breaker("news").call(
                    NewsSearchClient(config=config).asearch(query, max_results=max_results)
                ),
                "perplexity": lambda: get_breaker("perplexity").call(
                    PerplexityClient(config=config).asearch(query)
                ),
                "local_researcher": lambda: get_breaker("local_researcher").call(
                    LocalResearcherClient(config=config).asearch(query)
                ),
                "gnews": lambda: get_breaker("gnews").call(
                    GNewsClient(config=config).asearch(query, max_results=max_results)
                ),
                "vane": lambda: get_breaker("vane").call(
                    VaneClient(config=config).asearch(
                        query, focus_mode=kwargs.get("focus_mode", "webSearch")
                    )
                ),
                "khoj": lambda: get_breaker("khoj").call(
                    KhojClient(config=config).asearch(query)
                ),
                "fyin": lambda: get_breaker("fyin").call(
                    FyinClient(config=config).asearch(query)
                ),
                "meilisearch": lambda: get_breaker("meilisearch").call(
                    MeilisearchClient(config=config).asearch(query, max_results=max_results)
                ),
                "youtube": lambda: get_breaker("youtube").call(
                    YouTubeClient(config=config).asearch(query, max_results=max_results)
                ),
                "hackernews": lambda: get_breaker("hackernews").call(
                    HackerNewsClient(config=config).asearch(query, max_results=max_results)
                ),
                "reddit": lambda: get_breaker("reddit").call(
                    RedditClient(config=config).asearch(query, max_results=max_results)
                ),
                "github_repos": lambda: get_breaker("github_repos").call(
                    GithubReposClient(config=config).asearch(query, max_results=max_results)
                ),
                # Specialist engines (security/package/whois/code/ai_ml/archive/shopping)
                # are NOT built here — they are category-gated below (mirroring
                # _build_engines). Built unconditionally they ran for EVERY query, so a
                # general search needlessly hit osv/deps/whodat/zoekt/huggingface/
                # archive_org and the deal engines (and surfaced their noise).
            }

        engines = _make_engines()

        # ---- Router-gated: only add when query type matches (mirrors _build_engines) ----
        if category == QueryCategory.ACADEMIC:
            engines["arxiv"] = lambda: get_breaker("arxiv").call(
                ArxivClient(config=config).asearch(query, max_results=max_results)
            )
            # Gated on API key — see _build_engines comment for context.
            if config.semantic_scholar_api_key:
                engines["semantic_scholar"] = lambda: get_breaker("semantic_scholar").call(
                    SemanticScholarClient(config=config).asearch(query, max_results=max_results)
                )
            engines["openalex"] = lambda: get_breaker("openalex").call(
                OpenAlexClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.SECURITY:
            engines["osv"] = lambda: get_breaker("osv").call(
                OsvClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.PACKAGE:
            engines["deps"] = lambda: get_breaker("deps").call(
                DepsClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.WHOIS:
            engines["whodat"] = lambda: get_breaker("whodat").call(
                WhoDatClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.CODE:
            engines["zoekt"] = lambda: get_breaker("zoekt").call(
                ZoektClient(config=config).asearch(query, max_results=max_results)
            )
            # grepapp is gated — see _build_engines comment for context.
            if config.grepapp_enabled:
                engines["grepapp"] = lambda: get_breaker("grepapp").call(
                    GrepAppClient(config=config).asearch(query, max_results=max_results)
                )
            engines["github_code"] = lambda: get_breaker("github_code").call(
                GithubCodeClient(config=config).asearch(query, max_results=max_results)
            )

        if category == QueryCategory.AI_ML:
            engines["huggingface"] = lambda: get_breaker("huggingface").call(
                HuggingFaceClient(config=config).asearch(query, max_results=max_results)
            )

        # Archive: gated by category OR URL-shaped query (mirrors _build_engines).
        if category == QueryCategory.ARCHIVE or _URL_RE.search(query):
            engines["archive_org"] = lambda: get_breaker("archive_org").call(
                ArchiveOrgClient(config=config).asearch(query, max_results=max_results)
            )

        # Shopping/deals engines only for shopping queries (mirrors _build_engines).
        # They return generic current deals regardless of the query, so running them
        # on every search put Slickdeals/Newegg/etc. into unrelated answers.
        if category == QueryCategory.SHOPPING:
            engines["slickdeals"] = lambda: get_breaker("slickdeals").call(
                SlickdealsClient(config=config).asearch(query, max_results=max_results)
            )
            engines["cheapshark"] = lambda: get_breaker("cheapshark").call(
                CheapSharkClient(config=config).asearch(query, max_results=max_results)
            )
            engines["deals_rss"] = lambda: get_breaker("deals_rss").call(
                DealsRSSClient(config=config).asearch(query, max_results=max_results)
            )
            engines["priceghost"] = lambda: get_breaker("priceghost").call(
                PriceGhostClient(config=config).asearch(query, max_results=max_results)
            )
            engines["amazon_deals"] = lambda: get_breaker("amazon_deals").call(
                AmazonDealsClient(config=config).asearch(query, max_results=max_results)
            )
            engines["newegg"] = lambda: get_breaker("newegg").call(
                NeweggClient(config=config).asearch(query, max_results=max_results)
            )

        # Announce the full engine roster upfront so a live UI can paint the
        # whole checklist (queued) before any engine returns. The synthesizer
        # runs outside the tiers but belongs to tier1 for display.
        if on_event is not None:
            queued = {name: tier_of(name) for name in engines}
            queued["synthesizer"] = "tier1"
            await on_event({"type": "queued", "engines": queued})

        # Run synthesizer in parallel with tiered_search so we can keep its answer
        # first-class.  tiered_search now captures tuple answers for its own engines
        # (Perplexity/Vane/Khoj/Fyin/Local-Researcher), which were previously dropped.
        async def _run_synth() -> tuple[Any, BaseException | None, int]:
            if on_event is not None:
                await on_event({
                    "type": "engine", "engine": "synthesizer", "state": "running", "tier": "tier1",
                })
            res, exc, ms = await timed_call(get_breaker("synthesizer").call(
                SynthesizerClient(config=config).asearch(query)
            ))
            if on_event is not None:
                if exc is not None:
                    ev: dict[str, Any] = {
                        "state": "failed", "count": 0, "reason": failure_reason(exc), "ms": ms,
                    }
                else:
                    has_answer = isinstance(res, tuple) and len(res) == 2 and bool(res[0])
                    cnt = len(res[1]) if (
                        isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], list)
                    ) else 0
                    ev = {"state": "ok" if (cnt or has_answer) else "empty", "count": cnt, "ms": ms}
                    if has_answer:
                        ev["answer"] = True
                ev.update({"type": "engine", "engine": "synthesizer", "tier": "tier1"})
                await on_event(ev)
            return res, exc, ms

        synth_task = asyncio.ensure_future(_run_synth())
        tiered_task = asyncio.ensure_future(
            tiered_search(
                query, engines, category=category,
                max_results=max_results, include_slow=include_slow,
                on_event=on_event,
            )
        )
        synth_out, tiered_out = await asyncio.gather(
            synth_task, tiered_task, return_exceptions=True
        )

        # Unpack tiered output: (results, answers, status)
        if isinstance(tiered_out, BaseException):
            tiered_results: list[SearchResult] = []
            answers: dict[str, str] = {}
            engine_status: dict[str, dict[str, Any]] = {}
        else:
            tiered_results, tiered_answers, tiered_status = tiered_out
            answers = dict(tiered_answers)
            engine_status = dict(tiered_status)

        # Capture the synthesizer's answer + results + status separately (it runs
        # outside the tiers). timed_call returns (result, exc, elapsed_ms).
        synth_results: list[SearchResult] = []
        if isinstance(synth_out, BaseException):
            engine_status["synthesizer"] = {
                "state": "failed", "count": 0, "reason": failure_reason(synth_out),
            }
        else:
            synth_raw, synth_exc, synth_ms = synth_out
            if synth_exc is not None:
                engine_status["synthesizer"] = {
                    "state": "failed", "count": 0,
                    "reason": failure_reason(synth_exc), "ms": synth_ms,
                }
            else:
                if isinstance(synth_raw, tuple) and len(synth_raw) == 2:
                    raw_answer, raw_items = synth_raw
                    if raw_answer:
                        answers["synthesizer"] = raw_answer
                    if isinstance(raw_items, list):
                        synth_results = raw_items
                st_synth: dict[str, Any] = {
                    "state": "ok" if (synth_results or answers.get("synthesizer")) else "empty",
                    "count": len(synth_results), "ms": synth_ms, "results": synth_results,
                }
                if answers.get("synthesizer"):
                    st_synth["answer"] = True
                engine_status["synthesizer"] = st_synth

        all_results: list[SearchResult] = list(tiered_results) + synth_results

        if fuse:
            all_results = deduplicate_results(all_results)
            # Second stage: BM25 relevance rerank. The tiered path carries no RRF
            # fused_score (results are in tier/engine order, no relevance signal), so
            # off-topic-but-high-tier results (e.g. mwmbl listicles) surface. BM25
            # reorders by topical match to the query so the answer grounds on
            # on-topic results. No-op for queries with no usable terms.
            all_results = bm25_rerank(query, all_results)

        # Pull each engine's raw (pre-fusion) results out of status into a separate
        # per_engine map. This keeps engine_status JSON-serializable (it now holds
        # only metadata) while exposing the full per-source result lists for the
        # dashboard's "everything each source returned" view; per_engine[name] thus
        # matches engine_status[name]["count"] exactly (the panel/section counts
        # reconcile — both are the raw pre-fusion count, not the fused top-N).
        per_engine: dict[str, list[SearchResult]] = {
            name: st.pop("results", []) for name, st in engine_status.items()
        }

        # Fill the Meilisearch cache. This was only ever wired into the legacy flat
        # search(), so when smart mode became the default the index stopped being
        # written and "meilisearch" spent every tier1 sweep querying an empty one.
        # Awaited rather than fire-and-forget: ensure_future here races the end of
        # the event loop. Bounded so an unreachable Meilisearch can't stall a search.
        try:
            await asyncio.wait_for(
                self._cache_results(query, per_engine), timeout=_CACHE_WRITE_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, TimeoutError):
            pass

        answer_text = _select_best_answer(answers)
        message = f"Smart search [{category.value}]: {len(all_results)} results"
        return {
            "message": message,
            "answer": answer_text,
            "answers": answers,
            "results": all_results,
            "engine_status": engine_status,
            "per_engine": per_engine,
        }

    async def smart_search(
        self, query: str, *, include_slow: bool = False, fuse: bool = True, **kwargs,
    ) -> tuple[str, str, list[SearchResult]]:
        """Backward-compatible 3-tuple wrapper around :meth:`smart_search_rich`.

        Returns ``(status_message, answer_text, results)``.  ``answer_text`` is now
        the best available synthesized answer (Perplexity > Vane > Khoj > Fyin >
        Local Researcher > synthesizer), not synthesizer-only.
        """
        rich = await self.smart_search_rich(
            query, include_slow=include_slow, fuse=fuse, **kwargs,
        )
        return rich["message"], rich["answer"], rich["results"]
