from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.crawl4ai_client import Crawl4AIClient
from monster_search.config import Config
from monster_search.models import SearchResult

MOCK_RESPONSE = {
    "results": [
        {
            "markdown": {
                "raw_markdown": "# Example Page\n\nThis is the extracted content from the page.",
                "markdown_with_citations": "",
                "references_markdown": "",
                "fit_markdown": "",
                "fit_html": "",
            },
            "html": "<h1>Example Page</h1><p>This is the extracted content.</p>",
            "metadata": {"title": "Example Domain"},
        }
    ]
}


@respx.mock
def test_crawl4ai_search():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = Crawl4AIClient()
    message, results = client.search("https://example.com")
    assert "Example Page" in message
    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    assert results[0].source == "crawl4ai"
    assert results[0].title == "Example Domain"
    assert results[0].url == "https://example.com"


@respx.mock
def test_crawl4ai_search_empty():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = Crawl4AIClient()
    message, results = client.search("https://example.com")
    assert message == ""
    assert results == []


@respx.mock
def test_crawl4ai_search_custom_config():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(crawl4ai_url="http://localhost:11235")
    client = Crawl4AIClient(config=config)
    message, results = client.search("https://example.com")
    assert len(results) == 1


@respx.mock
def test_crawl4ai_search_error():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(500)
    )
    client = Crawl4AIClient()
    with pytest.raises(httpx.HTTPStatusError):
        client.search("https://example.com")


@respx.mock
@pytest.mark.asyncio
async def test_crawl4ai_async_search():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = Crawl4AIClient()
    message, results = await client.asearch("https://example.com")
    assert "Example Page" in message
    assert results[0].source == "crawl4ai"


@respx.mock
def test_crawl4ai_sends_correct_payload():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = Crawl4AIClient()
    client.search("https://example.com")
    request = respx.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["urls"] == ["https://example.com"]
    assert body["browser_config"]["type"] == "BrowserConfig"
    assert body["browser_config"]["params"]["headless"] is True
    assert body["crawler_config"]["type"] == "CrawlerRunConfig"


@respx.mock
def test_crawl4ai_wait_for_in_payload():
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    client = Crawl4AIClient()
    client.search("https://example.com", wait_for="div.jobs-list")
    request = respx.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["crawler_config"]["params"]["wait_for"] == "div.jobs-list"


@respx.mock
def test_crawl4ai_per_request_timeout():
    """Per-request timeout overrides config default."""
    respx.post("http://localhost:11235/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )
    config = Config(crawl4ai_timeout=120)
    client = Crawl4AIClient(config=config)
    # We can't easily assert the httpx timeout value, but we verify it doesn't crash
    message, results = client.search("https://example.com", timeout=30)
    assert len(results) == 1
