from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.gnews import GNewsClient, _strip_html
from monster_search.config import Config
from monster_search.models import SearchResult

# --- RSS feed mock data ---

RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>python programming - Google News</title>
    <link>https://news.google.com</link>
    <description>Google News</description>
    <item>
      <title>Python 3.14 Released with New Features - TechBlog</title>
      <link>https://news.google.com/rss/articles/CBMiK2h0dHBzOi8vdGVjaGJsb2cuY29tL3B5dGhvbi0zMTQtcmVsZWFzZWQ</link>
      <description>&lt;a href="https://techblog.com/python-314"&gt;Python 3.14&lt;/a&gt; has been released with &lt;b&gt;exciting new features&lt;/b&gt; including pattern matching improvements.</description>
      <pubDate>Mon, 24 Mar 2026 12:00:00 GMT</pubDate>
      <source url="https://techblog.com">TechBlog</source>
    </item>
    <item>
      <title>Best Python Libraries for Data Science in 2026 - DataMag</title>
      <link>https://news.google.com/rss/articles/CBMiLWh0dHBzOi8vZGF0YW1hZy5jb20vYmVzdC1weXRob24tbGlicmFyaWVz</link>
      <description>A comprehensive guide to the &lt;em&gt;top Python libraries&lt;/em&gt; for data science, machine learning, and AI in 2026.</description>
      <pubDate>Sun, 23 Mar 2026 08:00:00 GMT</pubDate>
      <source url="https://datamag.com">DataMag</source>
    </item>
    <item>
      <title>Python vs Rust: Performance Comparison - DevNews</title>
      <link>https://news.google.com/rss/articles/CBMiKmh0dHBzOi8vZGV2bmV3cy5jb20vcHl0aG9uLXZzLXJ1c3Q</link>
      <description>An in-depth &lt;b&gt;performance comparison&lt;/b&gt; between Python and Rust for backend services.</description>
      <pubDate>Sat, 22 Mar 2026 15:30:00 GMT</pubDate>
      <source url="https://devnews.com">DevNews</source>
    </item>
  </channel>
</rss>"""

EMPTY_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>xyznonexistent - Google News</title>
    <link>https://news.google.com</link>
    <description>Google News</description>
  </channel>
</rss>"""


# === Basic Search Tests ===


@respx.mock
def test_gnews_search():
    """Basic search returns parsed results with resolved URLs."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=RSS_FEED)
    )
    # Mock HEAD redirects for URL resolution
    respx.head("https://news.google.com/rss/articles/CBMiK2h0dHBzOi8vdGVjaGJsb2cuY29tL3B5dGhvbi0zMTQtcmVsZWFzZWQ").mock(
        return_value=httpx.Response(200, headers={"location": "https://techblog.com/python-314-released"}),
    )
    respx.head("https://news.google.com/rss/articles/CBMiLWh0dHBzOi8vZGF0YW1hZy5jb20vYmVzdC1weXRob24tbGlicmFyaWVz").mock(
        return_value=httpx.Response(200, headers={"location": "https://datamag.com/best-python-libraries"}),
    )
    respx.head("https://news.google.com/rss/articles/CBMiKmh0dHBzOi8vZGV2bmV3cy5jb20vcHl0aG9uLXZzLXJ1c3Q").mock(
        return_value=httpx.Response(200, headers={"location": "https://devnews.com/python-vs-rust"}),
    )
    client = GNewsClient()
    results = client.search("python programming")
    assert len(results) == 3
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "gnews"
    assert results[0].category == "news"
    assert "Python 3.14 Released" in results[0].title
    assert "TechBlog" in results[0].title  # source suffix kept
    assert results[0].published == "Mon, 24 Mar 2026 12:00:00 GMT"
    # HTML stripped from snippet
    assert "<" not in results[0].snippet
    assert "exciting new features" in results[0].snippet


@respx.mock
def test_gnews_max_results():
    """max_results limits the number of results."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=RSS_FEED)
    )
    respx.head(url__startswith="https://news.google.com/rss/articles/").mock(
        return_value=httpx.Response(200),
    )
    client = GNewsClient()
    results = client.search("python", max_results=1)
    assert len(results) == 1


@respx.mock
def test_gnews_empty_feed():
    """Empty RSS feed returns empty list."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=EMPTY_RSS_FEED)
    )
    client = GNewsClient()
    results = client.search("xyznonexistent")
    assert results == []


@respx.mock
def test_gnews_head_failure_fallback():
    """When HEAD redirect fails, falls back to Google redirect URL."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=RSS_FEED)
    )
    # All HEAD requests fail
    respx.head(url__startswith="https://news.google.com/rss/articles/").mock(
        side_effect=httpx.ConnectError("connection refused"),
    )
    client = GNewsClient()
    results = client.search("python", max_results=1)
    assert len(results) == 1
    # URL should be the original Google redirect URL
    assert "news.google.com" in results[0].url


@respx.mock
def test_gnews_sends_correct_params():
    """RSS request includes proper query params."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=EMPTY_RSS_FEED)
    )
    client = GNewsClient()
    client.search("test query")
    request = respx.calls[0].request
    url_str = str(request.url)
    assert "q=test" in url_str
    assert "hl=en-US" in url_str
    assert "gl=US" in url_str
    assert "ceid=US%3Aen" in url_str or "ceid=US:en" in url_str


# === Custom Config ===


@respx.mock
def test_gnews_custom_config():
    """Custom timeout from Config is respected."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=EMPTY_RSS_FEED)
    )
    config = Config(gnews_timeout=30)
    client = GNewsClient(config=config)
    results = client.search("test")
    assert results == []


# === Error Handling ===


@respx.mock
def test_gnews_http_error():
    """HTTP 500 propagates as HTTPStatusError."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(500)
    )
    client = GNewsClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test")


# === HTML Stripping ===


def test_strip_html():
    """_strip_html removes HTML tags."""
    assert _strip_html("<b>bold</b> text") == "bold text"
    assert _strip_html('<a href="url">link</a>') == "link"
    assert _strip_html("no tags") == "no tags"
    assert _strip_html("") == ""
    assert _strip_html("<em>nested <b>tags</b></em>") == "nested tags"


# === Async Tests ===


@respx.mock
@pytest.mark.asyncio
async def test_gnews_async_search():
    """Async search works."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=RSS_FEED)
    )
    respx.head(url__startswith="https://news.google.com/rss/articles/").mock(
        return_value=httpx.Response(200),
    )
    client = GNewsClient()
    results = await client.asearch("python programming", max_results=2)
    assert len(results) == 2
    assert results[0].source == "gnews"
    assert results[0].category == "news"


@respx.mock
@pytest.mark.asyncio
async def test_gnews_async_error():
    """Async HTTP error propagates."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(500)
    )
    client = GNewsClient()
    with pytest.raises(httpx.HTTPStatusError):
        await client.asearch("test")


@respx.mock
@pytest.mark.asyncio
async def test_gnews_async_empty():
    """Async empty feed returns empty list."""
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=EMPTY_RSS_FEED)
    )
    client = GNewsClient()
    results = await client.asearch("xyznonexistent")
    assert results == []
