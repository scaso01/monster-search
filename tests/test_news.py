from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monster_search.clients.news import NewsSearchClient
from monster_search.models import SearchResult


def _result(url: str, title: str, published: str = "") -> SearchResult:
    return SearchResult(
        url=url, title=title, snippet="", source="searxng", published=published
    )


def test_news_passes_category_news():
    mock = MagicMock(return_value=[_result("https://a.com", "A")])
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.search = mock
        client = NewsSearchClient()
        client.search("test news")
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs["category"] == "news"


def test_news_sorts_by_date_newest_first():
    results = [
        _result("https://old.com", "Old", "2026-03-01"),
        _result("https://new.com", "New", "2026-03-11"),
        _result("https://mid.com", "Mid", "2026-03-05"),
    ]
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.search = MagicMock(return_value=results)
        client = NewsSearchClient()
        _, sorted_results = client.search("test")
    assert sorted_results[0].title == "New"
    assert sorted_results[1].title == "Mid"
    assert sorted_results[2].title == "Old"


def test_news_items_without_dates_go_last():
    results = [
        _result("https://nodate.com", "NoDate", ""),
        _result("https://dated.com", "Dated", "2026-03-10"),
    ]
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.search = MagicMock(return_value=results)
        client = NewsSearchClient()
        _, sorted_results = client.search("test")
    assert sorted_results[0].title == "Dated"
    assert sorted_results[1].title == "NoDate"


def test_news_empty_results():
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.search = MagicMock(return_value=[])
        client = NewsSearchClient()
        message, results = client.search("nothing")
    assert results == []
    assert message == ""


@pytest.mark.asyncio
async def test_news_async_passes_category_news():
    mock = AsyncMock(return_value=[_result("https://a.com", "A")])
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.asearch = mock
        client = NewsSearchClient()
        await client.asearch("test news")
    mock.assert_called_once()
    _, kwargs = mock.call_args
    assert kwargs["category"] == "news"


@pytest.mark.asyncio
async def test_news_async_sorts_by_date():
    results = [
        _result("https://old.com", "Old", "2026-03-01"),
        _result("https://new.com", "New", "2026-03-11"),
    ]
    with patch("monster_search.clients.news.SearXNGClient") as MockSearXNG:
        MockSearXNG.return_value.asearch = AsyncMock(return_value=results)
        client = NewsSearchClient()
        _, sorted_results = await client.asearch("test")
    assert sorted_results[0].title == "New"
    assert sorted_results[1].title == "Old"
