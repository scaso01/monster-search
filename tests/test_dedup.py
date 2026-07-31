"""Tests for MinHash content deduplication."""

from __future__ import annotations

from monster_search._dedup import deduplicate_results, _shingle
from monster_search.models import SearchResult


def _result(
    title: str,
    url: str,
    source: str,
    snippet: str = "A sufficiently long snippet for minhash testing purposes.",
) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, source=source)


def test_empty_input():
    assert deduplicate_results([]) == []


def test_single_result():
    r = _result("A", "https://a.com", "searxng")
    assert deduplicate_results([r]) == [r]


def test_no_duplicates():
    results = [
        _result("Article about cats", "https://a.com", "searxng", "Cats are wonderful pets that bring joy."),
        _result("Guide to dogs", "https://b.com", "marginalia", "Dogs are loyal companions for families."),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 2


def test_url_dedup_same_url():
    """Same URL should be deduplicated, keeping higher-weight engine."""
    results = [
        _result("Page", "https://example.com/page", "meilisearch", "Some content here for testing."),
        _result("Page", "https://example.com/page", "searxng", "Some content here for testing."),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 1
    assert deduped[0].source == "searxng"  # weight 0.9 > 0.3


def test_url_dedup_normalized():
    """URLs that normalize to same value should be deduplicated."""
    results = [
        _result("Page", "http://www.example.com/page/", "meilisearch", "Short"),
        _result("Page", "https://example.com/page", "searxng", "Short"),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 1
    assert deduped[0].source == "searxng"


def test_content_dedup_near_duplicate():
    """Near-duplicate content from different URLs should be deduplicated."""
    long_text = "This is a comprehensive guide to understanding machine learning algorithms and their applications in modern data science workflows."
    results = [
        _result("ML Guide", "https://a.com/ml", "meilisearch", long_text),
        _result("ML Guide", "https://b.com/ml-copy", "searxng", long_text),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 1
    assert deduped[0].source == "searxng"  # higher weight kept


def test_content_dedup_keeps_higher_weight():
    """When duplicates found, keep result from highest-weighted engine."""
    text = "Detailed analysis of supply chain security vulnerabilities in open source software packages distributed through npm."
    results = [
        _result("Security", "https://a.com/sec", "zoekt", text),  # weight 0.4
        _result("Security", "https://b.com/sec2", "perplexity", text),  # weight 1.0
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 1
    assert deduped[0].source == "perplexity"


def test_short_text_falls_back_to_url():
    """Text shorter than 20 chars uses URL-only dedup."""
    results = [
        _result("A", "https://example.com", "meilisearch", "Short"),
        _result("B", "https://example.com", "searxng", "Brief"),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 1
    assert deduped[0].source == "searxng"


def test_preserves_original_order():
    """Deduplication should preserve the original order of kept results."""
    results = [
        _result("Python Tutorial", "https://first.com", "searxng", "Learn Python programming language from scratch with this comprehensive beginner guide covering variables, loops, and functions."),
        _result("Rust Ownership", "https://second.com", "marginalia", "Understanding the Rust borrow checker and ownership model for memory safety without garbage collection in systems programming."),
        _result("JavaScript Async", "https://third.com", "perplexity", "Master asynchronous JavaScript with promises, async/await patterns, and event loop mechanics for building responsive web applications."),
    ]
    deduped = deduplicate_results(results)
    assert len(deduped) == 3
    assert deduped[0].url == "https://first.com"
    assert deduped[1].url == "https://second.com"
    assert deduped[2].url == "https://third.com"


def test_shingle_basic():
    shingles = _shingle("hello", 3)
    assert shingles == ["hel", "ell", "llo"]


def test_shingle_short_text():
    assert _shingle("ab", 3) == ["ab"]
    assert _shingle("", 3) == []


def test_custom_threshold():
    """Lower threshold = more aggressive dedup."""
    text = "Detailed analysis of supply chain security vulnerabilities in open source software packages distributed through npm registries."
    results = [
        _result("Security A", "https://a.com", "searxng", text),
        _result("Security B", "https://b.com", "marginalia", text),
    ]
    # Low threshold should dedup identical content
    loose = deduplicate_results(results, threshold=0.3)
    assert len(loose) == 1

    # Default threshold (0.5) should also dedup identical content
    default = deduplicate_results(results, threshold=0.5)
    assert len(default) == 1
