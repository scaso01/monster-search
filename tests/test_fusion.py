"""Tests for the RRF fusion module."""

from __future__ import annotations

from monster_search.fusion import (
    fuse_results, bm25_rerank, ENGINE_WEIGHTS, DEFAULT_ENGINE_WEIGHT,
)
from monster_search.models import SearchResult


def _result(title: str, url: str, source: str, snippet: str = "snippet") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, source=source)


def test_fuse_empty():
    assert fuse_results({}) == []


def test_fuse_single_engine():
    results = {
        "searxng": [
            _result("A", "https://a.com", "searxng"),
            _result("B", "https://b.com", "searxng"),
        ],
    }
    fused = fuse_results(results)
    assert len(fused) == 2
    # First result should have higher RRF score (rank 1 > rank 2)
    assert fused[0].url == "https://a.com"
    assert fused[0].sources == ("searxng",)
    assert fused[0].source == "searxng"  # Single source, not "fused"
    assert fused[0].fused_score is not None
    assert fused[0].fused_score > fused[1].fused_score


def test_fuse_two_engines_overlap():
    results = {
        "searxng": [
            _result("A SearXNG", "https://a.com", "searxng", "snippet from searxng"),
            _result("B", "https://b.com", "searxng"),
        ],
        "marginalia": [
            _result("A Marginalia Longer Title", "https://a.com", "marginalia", "snippet from marginalia"),
            _result("C", "https://c.com", "marginalia"),
        ],
    }
    fused = fuse_results(results)
    assert len(fused) == 3

    # URL a.com found by both engines — should be ranked highest
    top = fused[0]
    assert top.url == "https://a.com"
    assert top.sources == ("searxng", "marginalia")
    assert top.source == "fused"
    assert top.fused_score > fused[1].fused_score
    # Longest title wins
    assert top.title == "A Marginalia Longer Title"
    # Snippets merged
    assert "searxng" in top.snippet
    assert "marginalia" in top.snippet


def test_fuse_no_overlap():
    results = {
        "searxng": [_result("A", "https://a.com", "searxng")],
        "marginalia": [_result("B", "https://b.com", "marginalia")],
    }
    fused = fuse_results(results)
    assert len(fused) == 2
    urls = {r.url for r in fused}
    assert urls == {"https://a.com", "https://b.com"}
    # Same rank but different engine weights: searxng (0.9) > marginalia (0.85)
    assert fused[0].url == "https://a.com"
    assert fused[0].fused_score > fused[1].fused_score


def test_fuse_rrf_scoring():
    """Verify weighted RRF formula: score = weight * 1/(k+rank), k=60 default."""
    results = {
        "engine1": [_result("A", "https://a.com", "e1")],
    }
    fused = fuse_results(results, k=60)
    # rank=1, unknown engine weight=0.5, so score = 0.5/(60+1) = 0.5/61
    expected = round(0.5 / 61, 6)
    assert fused[0].fused_score == expected


def test_fuse_metadata_merge_published():
    results = {
        "searxng": [SearchResult(
            title="A", url="https://a.com", snippet="s", source="searxng",
            published="2026-01-01",
        )],
        "marginalia": [SearchResult(
            title="A", url="https://a.com", snippet="s", source="marginalia",
        )],
    }
    fused = fuse_results(results)
    assert fused[0].published == "2026-01-01"


def test_fuse_many_snippets_picks_longest():
    """When >3 unique snippets, pick the longest instead of concatenating."""
    results = {
        f"engine{i}": [_result("A", "https://a.com", f"e{i}", f"snippet {'x' * i}")]
        for i in range(5)
    }
    fused = fuse_results(results)
    # Should pick longest, not concatenate 5 snippets
    assert "|" not in fused[0].snippet
    assert "xxxx" in fused[0].snippet


# --- Weighted RRF tests ---


def test_engine_weights_exist():
    """ENGINE_WEIGHTS should contain known engines."""
    assert "perplexity" in ENGINE_WEIGHTS
    assert "searxng" in ENGINE_WEIGHTS
    assert "meilisearch" in ENGINE_WEIGHTS
    assert ENGINE_WEIGHTS["perplexity"] == 1.0
    assert ENGINE_WEIGHTS["meilisearch"] == 0.3


def test_default_engine_weight():
    assert DEFAULT_ENGINE_WEIGHT == 0.5


def test_weighted_rrf_higher_weight_ranks_higher():
    """A result from a higher-weighted engine should score higher than one
    from a lower-weighted engine, when both are at the same rank."""
    results = {
        "perplexity": [_result("A", "https://a.com", "perplexity")],  # weight 1.0
        "meilisearch": [_result("B", "https://b.com", "meilisearch")],  # weight 0.3
    }
    fused = fuse_results(results, k=60)
    # Both at rank 1, but perplexity has higher weight
    assert fused[0].url == "https://a.com"
    assert fused[0].fused_score > fused[1].fused_score


def test_weighted_rrf_formula():
    """Verify weighted RRF formula: score = weight * 1/(k+rank)."""
    results = {
        "searxng": [_result("A", "https://a.com", "searxng")],  # weight 0.9
    }
    fused = fuse_results(results, k=60)
    expected = round(0.9 * 1.0 / 61, 6)
    assert fused[0].fused_score == expected


def test_weighted_rrf_unknown_engine_uses_default():
    """Unknown engines should use DEFAULT_ENGINE_WEIGHT (0.5)."""
    results = {
        "totally_unknown_engine": [_result("A", "https://a.com", "unknown")],
    }
    fused = fuse_results(results, k=60)
    expected = round(0.5 * 1.0 / 61, 6)
    assert fused[0].fused_score == expected


def test_weighted_rrf_overlap_accumulates():
    """Overlapping URL from multiple engines accumulates weighted scores."""
    results = {
        "searxng": [_result("A", "https://a.com", "searxng")],  # 0.9 * 1/61
        "marginalia": [_result("A", "https://a.com", "marginalia")],  # 0.85 * 1/61
    }
    fused = fuse_results(results, k=60)
    expected = round((0.9 + 0.85) / 61, 6)
    assert fused[0].fused_score == expected


def test_weighted_rrf_low_weight_low_rank_beaten_by_high_weight():
    """A rank-1 result from a low-weight engine can be beaten by
    a rank-1 result from a high-weight engine."""
    results = {
        "meilisearch": [
            _result("A", "https://a.com", "meilisearch"),  # 0.3/61
            _result("B", "https://b.com", "meilisearch"),  # 0.3/62
        ],
        "perplexity": [
            _result("C", "https://c.com", "perplexity"),  # 1.0/61
        ],
    }
    fused = fuse_results(results, k=60)
    # C from perplexity should rank above A from meilisearch
    assert fused[0].url == "https://c.com"


# --- BM25 rerank tests ---


def test_bm25_rerank_sinks_generic_only_matches():
    """Junk matching only ubiquitous query words ('open'/'source') sinks below a
    result that matches the rare topical words ('coding'/'agents') — the exact
    failure a naive keyword-overlap filter could not catch."""
    query = "best open source ai coding agents 2026"
    results = [
        _result("Best Open Source Music Generators 2024", "https://m.com", "mwmbl",
                "browse free open source music software"),
        _result("Best Open Source BSD Software 2026", "https://bsd.com", "mwmbl",
                "open source bsd torrent client"),
        _result("Best Open Source AI Coding Tools 2026", "https://ai.com", "web",
                "open source ai coding agents reviewed"),
    ]
    out = bm25_rerank(query, results)
    assert out[0].url == "https://ai.com"                       # topical result first
    assert out[-1].url in {"https://m.com", "https://bsd.com"}  # generic junk sinks


def test_bm25_rerank_topical_result_rises_from_last():
    query = "rust tokio async cancellation"
    results = [
        _result("Open source news roundup", "https://n.com", "mwmbl", "general tech news"),
        _result("Best open source software 2026", "https://s.com", "mwmbl", "browse software"),
        _result("Tokio async cancellation safety in Rust", "https://r.com", "web",
                "rust tokio select cancellation"),
    ]
    out = bm25_rerank(query, results)
    assert out[0].url == "https://r.com"


def test_bm25_rerank_noop_on_few_results():
    one = [_result("A", "https://a.com", "web")]
    assert bm25_rerank("any query", one) == one


def test_bm25_rerank_noop_on_empty_query_preserves_order():
    results = [
        _result("A", "https://a.com", "web"),
        _result("B", "https://b.com", "web"),
    ]
    # "a" is a single char → no usable terms → order preserved, not scrambled.
    out = bm25_rerank("a", results)
    assert [r.url for r in out] == ["https://a.com", "https://b.com"]


def test_bm25_rerank_generic_query_preserves_order():
    """If every candidate matches all query terms equally (zero discriminating IDF),
    the rerank must NOT scramble the order — the base signal (position) carries."""
    query = "open source"
    results = [
        _result("Open source one", "https://1.com", "web", "open source"),
        _result("Open source two", "https://2.com", "web", "open source"),
        _result("Open source three", "https://3.com", "web", "open source"),
    ]
    out = bm25_rerank(query, results)
    assert [r.url for r in out] == ["https://1.com", "https://2.com", "https://3.com"]


def test_bm25_rerank_blends_with_fused_score():
    """When results carry RRF fused_score, BM25 blends with it: strong topical match
    on a low-scored result outranks an off-topic high-scored one."""
    r_offtopic = SearchResult(title="Networking software", url="https://n.com",
                              snippet="open source networking", source="searxng",
                              fused_score=0.9)
    r_ontopic = SearchResult(title="Rust async tokio guide", url="https://r.com",
                             snippet="rust async tokio", source="mwmbl",
                             fused_score=0.1)
    out = bm25_rerank("rust async tokio", [r_offtopic, r_ontopic])
    assert out[0].url == "https://r.com"
