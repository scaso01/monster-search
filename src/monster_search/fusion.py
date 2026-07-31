"""Reciprocal Rank Fusion (RRF) for combining results from multiple engines."""

from __future__ import annotations

import math
import re

from monster_search.models import SearchResult

# Per-engine quality weights for weighted RRF scoring.
# Higher weight = more trusted source. Unknown engines default to 0.5.
ENGINE_WEIGHTS: dict[str, float] = {
    "perplexity": 1.0,
    "searxng": 0.9,
    "marginalia": 0.85,
    # mwmbl is a keyword index that returns low-precision listicle/category pages
    # (e.g. "Best Open Source <unrelated topic>"); it was over-trusted near searxng's
    # level. Dropped to default so its solo results don't outrank better engines in
    # RRF and lose dedup tie-breaks to them. Relevance is handled by bm25_rerank.
    "mwmbl": 0.5,
    "fyin": 0.8,
    "news": 0.8,
    "gnews": 0.75,
    "archive_org": 0.7,
    "openalex": 0.7,
    "arxiv": 0.7,
    "semantic_scholar": 0.7,
    "synthesizer": 0.85,
    "vane": 0.65,
    "khoj": 0.6,
    "local_researcher": 0.6,
    "osv": 0.5,
    "deps": 0.5,
    "whodat": 0.4,
    "zoekt": 0.4,
    "meilisearch": 0.3,
    # Shopping engines
    "searxng_shopping": 0.8,
    "slickdeals": 0.7,
    "cheapshark": 0.7,
    "deals_rss": 0.6,
}

DEFAULT_ENGINE_WEIGHT: float = 0.5


def fuse_results(
    results_by_engine: dict[str, list[SearchResult]],
    k: int = 60,
) -> list[SearchResult]:
    """Fuse ranked lists from multiple engines using weighted RRF with metadata merge.

    Weighted RRF score for a URL = sum(weight * 1 / (k + rank)) across all engines.
    weight comes from ENGINE_WEIGHTS (default 0.5 for unknown engines).
    k=60 is the standard constant from Cormack et al. (2009).
    """
    if not results_by_engine:
        return []

    # Accumulate RRF scores and collect metadata per URL
    url_scores: dict[str, float] = {}
    url_sources: dict[str, list[str]] = {}
    url_results: dict[str, list[SearchResult]] = {}

    for engine_name, results in results_by_engine.items():
        weight = ENGINE_WEIGHTS.get(engine_name, DEFAULT_ENGINE_WEIGHT)
        for rank, result in enumerate(results, start=1):
            url = result.url
            url_scores[url] = url_scores.get(url, 0.0) + weight / (k + rank)

            if url not in url_sources:
                url_sources[url] = []
                url_results[url] = []
            url_sources[url].append(engine_name)
            url_results[url].append(result)

    # Build fused results with metadata merge
    fused: list[SearchResult] = []
    for url in url_scores:
        candidates = url_results[url]
        sources = tuple(url_sources[url])
        score = url_scores[url]

        # Title: pick the longest non-empty
        title = max((r.title for r in candidates), key=len, default="")

        # Snippet: merge unique snippets
        seen_snippets: list[str] = []
        for r in candidates:
            if r.snippet and r.snippet not in seen_snippets:
                seen_snippets.append(r.snippet)
        if len(seen_snippets) > 3:
            snippet = max(seen_snippets, key=len)
        else:
            snippet = " | ".join(seen_snippets)

        # Take first non-None for optional fields
        published = next((r.published for r in candidates if r.published), None)
        category = next((r.category for r in candidates if r.category), None)
        engine = next((r.engine for r in candidates if r.engine), None)
        price = next((r.price for r in candidates if r.price), None)
        in_stock = next((r.in_stock for r in candidates if r.in_stock is not None), None)

        fused.append(SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            source="fused" if len(sources) > 1 else sources[0],
            engine=engine,
            score=score,
            published=published,
            category=category,
            sources=sources,
            fused_score=round(score, 6),
            price=price,
            in_stock=in_stock,
        ))

    # Sort by descending fused score
    fused.sort(key=lambda r: r.fused_score or 0.0, reverse=True)
    return fused


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (drops punctuation, keeps numbers like '2026')."""
    return _TOKEN_RE.findall((text or "").lower())


def bm25_rerank(
    query: str,
    results: list[SearchResult],
    *,
    alpha: float = 0.7,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[SearchResult]:
    """Re-rank fused results by lexical relevance to the query (BM25), blended with
    the existing ranking signal. Pure stdlib — no deps, no embeddings.

    Why BM25 and not a plain keyword overlap filter: BM25 weights each query term by
    IDF over the candidate set, so words that appear in nearly every candidate
    ('open', 'source', 'best') count for almost nothing while rare topical words
    ('coding', 'agents') dominate. A naive shared-term filter keeps "Best Open Source
    Music Generators" for the query "best open source AI coding agents" (it shares
    'open'/'source'); BM25 scores it ~0 because those terms have ~0 IDF here.

    Blends BM25 (normalized) with the existing base signal via a convex combination
    (``alpha`` toward BM25) rather than hard-filtering, so a weak-but-unique result
    degrades gracefully instead of vanishing. Base signal is ``fused_score`` when the
    list carries one (RRF output), else the input position (the caller's existing
    order). No-op when the query has no usable terms or there are <2 results, so a
    generic-only query can't nuke the list.
    """
    q_terms = list({t for t in _tokenize(query) if len(t) > 1})
    n_docs = len(results)
    if n_docs < 2 or not q_terms:
        return list(results)

    docs = [_tokenize(f"{r.title} {r.snippet}") for r in results]
    avgdl = (sum(len(d) for d in docs) / n_docs) or 1.0
    df = {t: sum(1 for d in docs if t in d) for t in q_terms}

    # Quasi-stopword pruning: drop query terms that appear in the MAJORITY of
    # candidates — on this candidate set they carry no discriminating signal. For
    # "best open source AI coding agents 2026", the listicle boilerplate
    # best/open/source/2026 matches most pages, so plain BM25 still credited a
    # "Best Open Source BSD Software 2026" page for 4 shared words. Pruning them makes
    # a page that matches ONLY boilerplate score ~0 and sink, while the distinctive
    # terms (coding/agents) decide the ranking. If EVERY term is generic (e.g. the
    # whole query is "open source"), nothing discriminating is left → no-op.
    discriminating = [t for t in q_terms if df[t] <= n_docs / 2]
    if not discriminating:
        return list(results)
    q_terms = discriminating

    def _idf(term: str) -> float:
        n = df[term]
        # +1 inside log floors a term present in every doc at ~0 (never negative).
        return math.log(1 + (n_docs - n + 0.5) / (n + 0.5))

    bm: list[float] = []
    for doc in docs:
        dl = len(doc) or 1
        score = 0.0
        for term in q_terms:
            tf = doc.count(term)
            if tf:
                score += _idf(term) * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))
        bm.append(score)

    max_bm = max(bm) or 1.0
    # Base signal: RRF fused_score if present, else preserve the caller's order.
    if any((r.fused_score or 0.0) for r in results):
        max_base = max((r.fused_score or 0.0) for r in results) or 1.0
        base = [(r.fused_score or 0.0) / max_base for r in results]
    else:
        base = [(n_docs - i) / n_docs for i in range(n_docs)]

    blended = [alpha * (bm[i] / max_bm) + (1 - alpha) * base[i] for i in range(n_docs)]
    order = sorted(range(n_docs), key=lambda i: blended[i], reverse=True)
    return [results[i] for i in order]
