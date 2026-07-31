from __future__ import annotations

from monster_search.models import SearchResult


def test_search_result_creation():
    result = SearchResult(
        title="Example",
        url="https://example.com",
        snippet="A test result",
        source="searxng",
    )
    assert result.title == "Example"
    assert result.url == "https://example.com"
    assert result.snippet == "A test result"
    assert result.source == "searxng"
    assert result.engine is None
    assert result.score is None
    assert result.published is None
    assert result.category is None


def test_search_result_brief():
    result = SearchResult(
        title="Example Page",
        url="https://example.com/page",
        snippet="This is a longer snippet that describes the page content in detail.",
        source="searxng",
    )
    brief = result.brief()
    assert "Example Page" in brief
    assert "https://example.com/page" in brief
    assert "This is a longer snippet" in brief


def test_search_result_brief_truncates_long_snippets():
    result = SearchResult(
        title="Test",
        url="https://example.com",
        snippet="x" * 300,
        source="searxng",
    )
    brief = result.brief()
    # Brief snippet should be capped at 200 chars + ellipsis
    assert len(brief.split("\n")[2].strip()) <= 204
