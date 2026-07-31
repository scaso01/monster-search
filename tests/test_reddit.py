"""Tests for Reddit search client (RSS/Atom feed)."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.reddit import RedditClient
from monster_search.models import SearchResult


REDDIT_RSS_RESPONSE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>reddit search: search aggregators</title>
  <entry>
    <title>Best search aggregators in 2026</title>
    <link href="https://old.reddit.com/r/selfhosted/comments/abc123/best_search_aggregators/"/>
    <updated>2024-03-15T10:53:20+00:00</updated>
    <category term="selfhosted" label="r/selfhosted"/>
    <content type="html">I've been looking for a good search tool that combines multiple sources...</content>
  </entry>
  <entry>
    <title>SearXNG vs alternatives</title>
    <link href="https://old.reddit.com/r/selfhosted/comments/def456/searxng_vs_alternatives/"/>
    <updated>2024-03-14T07:06:40+00:00</updated>
    <category term="selfhosted" label="r/selfhosted"/>
    <content type="html"></content>
  </entry>
</feed>
"""

REDDIT_RSS_EMPTY = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>reddit search: xyznonexistent123</title>
</feed>
"""


@respx.mock
def test_reddit_search():
    """Basic search returns SearchResults with subreddit in title."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_RESPONSE)
    )
    client = RedditClient()
    results = client.search("search aggregators", max_results=5)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "reddit"
    assert "[r/selfhosted]" in results[0].title
    assert "Best search aggregators" in results[0].title
    assert "reddit.com" in results[0].url
    assert "/r/selfhosted/comments/abc123/" in results[0].url
    assert "looking for a good search tool" in results[0].snippet


@respx.mock
def test_reddit_empty():
    """Empty feed returns empty list."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_EMPTY)
    )
    client = RedditClient()
    results = client.search("xyznonexistent123")
    assert results == []


@respx.mock
def test_reddit_error():
    """HTTP 500 raises HTTPStatusError."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(500)
    )
    client = RedditClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


@respx.mock
def test_reddit_timeout():
    """ReadTimeout raises TimeoutException."""
    respx.get("https://www.reddit.com/search.rss").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = RedditClient()
    with pytest.raises(httpx.TimeoutException):
        client.search("test")


@respx.mock
def test_reddit_sends_correct_params():
    """Verify q, limit, sort=relevance, type=link sent to RSS endpoint."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_EMPTY)
    )
    client = RedditClient()
    client.search("python asyncio", max_results=10)

    request = respx.calls[0].request
    url_str = str(request.url)
    assert "q=python" in url_str
    assert "limit=10" in url_str
    assert "sort=relevance" in url_str
    assert "type=link" in url_str


@respx.mock
def test_reddit_empty_content_snippet():
    """When content is empty, snippet is empty string."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_RESPONSE)
    )
    client = RedditClient()
    results = client.search("searxng", max_results=5)

    # Second entry has empty content
    assert results[1].snippet == ""


@respx.mock
def test_reddit_published_format():
    """Atom updated timestamp formatted as 'YYYY-MM-DD HH:MM UTC'."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_RESPONSE)
    )
    client = RedditClient()
    results = client.search("test", max_results=5)

    assert results[0].published is not None
    assert results[0].published == "2024-03-15 10:53 UTC"


@respx.mock
@pytest.mark.asyncio
async def test_reddit_async_search():
    """Async search works."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_RESPONSE)
    )
    client = RedditClient()
    results = await client.asearch("search aggregators", max_results=5)

    assert len(results) == 2
    assert results[0].source == "reddit"
    assert "[r/selfhosted]" in results[0].title


@respx.mock
def test_reddit_fetches_from_www_not_old():
    """Regression: old.reddit.com answers /search.rss with HTML, not Atom.

    It returns HTTP 200 while doing so, so nothing fails until the parser,
    and every mocked test still passed while the live engine was broken.
    """
    route = respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_EMPTY)
    )
    RedditClient().search("test")

    assert route.called
    assert "old.reddit.com" not in str(respx.calls[0].request.url)


@respx.mock
def test_reddit_wellformed_html_is_not_mistaken_for_an_empty_feed():
    """A blocked request arrives as an HTML page with a 200 status.

    This sample happens to be well-formed XML, so it parses without error and
    would be reported as "no results" unless the root element is checked. A
    wrong answer that looks right is worse than a failure.
    """
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text="<!DOCTYPE html><html><body>no</body></html>")
    )
    with pytest.raises(RuntimeError, match="did not return an Atom feed"):
        RedditClient().search("test")


@respx.mock
def test_reddit_malformed_html_gives_a_readable_error():
    """The real interstitial is not well-formed, so it fails in the parser."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(
            200, text='<!DOCTYPE html><html lang="en" device=desktop><head prefix="og: x"></html>'
        )
    )
    with pytest.raises(RuntimeError, match="did not return an Atom feed"):
        RedditClient().search("test")


@respx.mock
def test_reddit_url_normalized():
    """old.reddit.com URLs are normalized to reddit.com."""
    respx.get("https://www.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=REDDIT_RSS_RESPONSE)
    )
    client = RedditClient()
    results = client.search("test", max_results=5)

    assert "old.reddit.com" not in results[0].url
    assert "reddit.com" in results[0].url
