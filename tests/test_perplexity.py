from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from monster_search.clients.perplexity_client import PerplexityClient
from monster_search.config import Config
from monster_search.models import SearchResult


def test_perplexity_missing_token():
    config = Config(perplexity_session_token="")
    client = PerplexityClient(config=config)
    with pytest.raises(ValueError, match="session token required"):
        client.search("test")


def test_perplexity_missing_library():
    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)
    with patch.dict("sys.modules", {"curl_cffi": None, "curl_cffi.requests": None}):
        with pytest.raises(ImportError, match="curl_cffi"):
            client.search("test")


def test_perplexity_parse_sse():
    """Test SSE response parsing with a realistic payload."""
    import json

    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)

    answer_data = json.dumps({
        "answer": "Paris is the capital of France.",
        "web_results": [
            {"name": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Paris", "snippet": "Paris is the capital..."},
            {"name": "Britannica", "url": "https://britannica.com/place/Paris", "snippet": "City and capital of France"},
        ],
    })

    steps = json.dumps([
        {
            "step_type": "SEARCH_RESULTS",
            "content": {
                "results": [
                    {"name": "Result 1", "url": "https://example.com/1", "snippet": "First result"},
                ]
            },
        },
        {
            "step_type": "FINAL",
            "content": {"answer": answer_data},
        },
    ])

    sse_text = f"data: {json.dumps({'text': steps, 'status': 'COMPLETED'})}\n"

    answer, sources = client._parse_sse(sse_text)
    assert answer == "Paris is the capital of France."
    assert len(sources) == 3  # 1 from SEARCH_RESULTS + 2 from FINAL
    assert sources[0].url == "https://example.com/1"
    assert sources[0].source == "perplexity"
    assert sources[1].url == "https://en.wikipedia.org/wiki/Paris"


def test_perplexity_parse_sse_failed_status():
    """Test that FAILED status raises RuntimeError."""
    import json

    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)

    sse_text = f"data: {json.dumps({'status': 'FAILED', 'text': 'Rate limited'})}\n"

    with pytest.raises(RuntimeError, match="Perplexity query failed"):
        client._parse_sse(sse_text)


def test_perplexity_search():
    """Test full search flow with mocked curl_cffi Session."""
    import json

    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)

    answer_data = json.dumps({
        "answer": "AI synthesis of the results.",
        "web_results": [
            {"name": "Result 1", "url": "https://example.com/1", "snippet": "First result"},
            {"name": "Result 2", "url": "https://example.com/2", "snippet": "Second result"},
        ],
    })

    steps = json.dumps([
        {"step_type": "FINAL", "content": {"answer": answer_data}},
    ])

    sse_response = f"data: {json.dumps({'text': steps, 'status': 'COMPLETED'})}\n"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = sse_response
    mock_resp.raise_for_status = MagicMock()

    mock_get_resp = MagicMock()
    mock_get_resp.status_code = 200

    mock_session = MagicMock()
    mock_session.get.return_value = mock_get_resp
    mock_session.post.return_value = mock_resp
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    with patch("monster_search.clients.perplexity_client.PerplexityClient.search") as mock_search:
        # Actually test the real flow by mocking curl_cffi.requests.Session
        pass

    # Direct approach: mock at the curl_cffi level
    mock_session_cls = MagicMock(return_value=mock_session)

    with patch.dict("sys.modules", {"curl_cffi": MagicMock(), "curl_cffi.requests": MagicMock(Session=mock_session_cls)}):
        # Re-import to pick up the mock — instead, patch at call site
        pass

    # Simplest: test _parse_sse directly (already covered above) and test the full flow
    # by patching the Session class after import
    import sys
    mock_curl_cffi = MagicMock()
    mock_requests_module = MagicMock()
    mock_requests_module.Session = MagicMock(return_value=mock_session)

    with patch.dict(sys.modules, {
        "curl_cffi": mock_curl_cffi,
        "curl_cffi.requests": mock_requests_module,
    }):
        message, results = client.search("test query")
        assert "AI synthesis" in message
        assert len(results) == 2
        assert results[0].source == "perplexity"
        assert results[0].url == "https://example.com/1"


def test_perplexity_403_raises():
    """Test that 403 response raises ValueError with refresh instructions."""
    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)

    mock_resp = MagicMock()
    mock_resp.status_code = 403

    mock_session = MagicMock()
    mock_session.get.return_value = MagicMock()
    mock_session.post.return_value = mock_resp
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)

    import sys
    mock_requests_module = MagicMock()
    mock_requests_module.Session = MagicMock(return_value=mock_session)

    with patch.dict(sys.modules, {
        "curl_cffi": MagicMock(),
        "curl_cffi.requests": mock_requests_module,
    }):
        with pytest.raises(ValueError, match="403"):
            client.search("test")


@pytest.mark.asyncio
async def test_perplexity_async_wraps_sync():
    config = Config(perplexity_session_token="fake-token")
    client = PerplexityClient(config=config)

    with patch.object(client, "search") as mock_search:
        mock_search.return_value = ("answer", [])
        message, results = await client.asearch("test")
        assert message == "answer"
        mock_search.assert_called_once_with("test")
