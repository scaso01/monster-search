"""Tests for YouTube search + transcript client."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from monster_search.clients.youtube import YouTubeClient
from monster_search.models import SearchResult


# --- Mock data ---

MOCK_ENTRIES = [
    {
        "id": "abc123",
        "title": "Rust Async Patterns Explained",
        "url": "https://www.youtube.com/watch?v=abc123",
        "webpage_url": "https://www.youtube.com/watch?v=abc123",
        "channel": "Crust of Rust",
        "upload_date": "20240115",
        "description": "A deep dive into async patterns in Rust.",
        "duration": 3600,
    },
    {
        "id": "def456",
        "title": "Python Type Hints Guide",
        "url": "https://www.youtube.com/watch?v=def456",
        "webpage_url": "https://www.youtube.com/watch?v=def456",
        "channel": "ArjanCodes",
        "upload_date": "20240220",
        "description": "Complete guide to Python type hints.",
        "duration": 1800,
    },
]


@dataclass
class MockSnippet:
    text: str
    start: float
    duration: float


MOCK_TRANSCRIPT_SNIPPETS = [
    MockSnippet(text="Hello everyone, welcome back.", start=0.0, duration=2.5),
    MockSnippet(text="Today we talk about async patterns.", start=2.5, duration=3.0),
    MockSnippet(text="Let's start with the basics.", start=5.5, duration=2.0),
]

MOCK_EXTRACT_INFO = {"entries": MOCK_ENTRIES}


def _make_fetched_transcript(snippets):
    """Create a mock FetchedTranscript-like object that is iterable."""
    mock_fetched = MagicMock()
    mock_fetched.__iter__ = MagicMock(return_value=iter(snippets))
    return mock_fetched


# --- Unit tests ---


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_search(mock_ytdlp, mock_transcript_api_cls):
    """Basic search returns SearchResults with transcripts."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = MOCK_EXTRACT_INFO
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = _make_fetched_transcript(MOCK_TRANSCRIPT_SNIPPETS)
    mock_transcript_api_cls.return_value = mock_api_instance

    client = YouTubeClient()
    results = client.search("rust async", max_results=2)

    assert len(results) == 2
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].source == "youtube"
    assert results[0].url == "https://www.youtube.com/watch?v=abc123"
    assert "Rust Async Patterns Explained" in results[0].title
    assert "Crust of Rust" in results[0].title
    assert "Hello everyone" in results[0].snippet
    assert results[0].published == "2024-01-15"
    assert results[0].category == "video"


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_no_transcript_falls_back_to_description(mock_ytdlp, mock_transcript_api_cls):
    """When transcript fails, snippet uses video description."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = MOCK_EXTRACT_INFO
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.side_effect = Exception("No transcript")
    mock_transcript_api_cls.return_value = mock_api_instance

    client = YouTubeClient()
    results = client.search("rust async", max_results=2)

    assert len(results) == 2
    assert results[0].snippet == "A deep dive into async patterns in Rust."


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_truncates_long_transcript(mock_ytdlp, mock_transcript_api_cls):
    """Long transcripts are truncated to youtube_max_transcript_chars."""
    mock_ydl = MagicMock()
    single_entry = {"entries": [MOCK_ENTRIES[0]]}
    mock_ydl.extract_info.return_value = single_entry
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    long_snippets = [MockSnippet(text="word " * 100, start=float(i), duration=1.0) for i in range(50)]
    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = _make_fetched_transcript(long_snippets)
    mock_transcript_api_cls.return_value = mock_api_instance

    from monster_search.config import Config
    config = Config(youtube_max_transcript_chars=100)
    client = YouTubeClient(config=config)
    results = client.search("test", max_results=1)

    assert len(results[0].snippet) <= 103  # 100 + "..."


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_empty_results(mock_ytdlp, mock_transcript_api_cls):
    """Empty search results return empty list."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = {"entries": []}
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    client = YouTubeClient()
    results = client.search("obscure query xyz", max_results=5)

    assert results == []


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_none_entries(mock_ytdlp, mock_transcript_api_cls):
    """None entries handled gracefully."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = {"entries": None}
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    client = YouTubeClient()
    results = client.search("test", max_results=5)

    assert results == []


@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_extract_error(mock_ytdlp):
    """yt-dlp extraction error raises RuntimeError."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.side_effect = Exception("Network error")
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    client = YouTubeClient()
    with pytest.raises(RuntimeError, match="YouTube search failed"):
        client.search("test")


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_max_results_honored(mock_ytdlp, mock_transcript_api_cls):
    """max_results limits number of returned results."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = MOCK_EXTRACT_INFO
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = _make_fetched_transcript(MOCK_TRANSCRIPT_SNIPPETS)
    mock_transcript_api_cls.return_value = mock_api_instance

    client = YouTubeClient()
    results = client.search("test", max_results=1)

    # The count is the thing this test is named for. Asserting only the query
    # string left the returned list unchecked, so a client that ignored
    # max_results entirely would still have passed.
    assert len(results) == 1

    call_args = mock_ydl.extract_info.call_args
    assert "ytsearch1:" in call_args[0][0]


@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
def test_youtube_published_date_format(mock_ytdlp, mock_transcript_api_cls):
    """upload_date '20240115' is formatted as '2024-01-15'."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = {"entries": [MOCK_ENTRIES[0]]}
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = _make_fetched_transcript(MOCK_TRANSCRIPT_SNIPPETS)
    mock_transcript_api_cls.return_value = mock_api_instance

    client = YouTubeClient()
    results = client.search("test", max_results=1)

    assert results[0].published == "2024-01-15"


@pytest.mark.asyncio
@patch("monster_search.clients.youtube.YouTubeTranscriptApi")
@patch("monster_search.clients.youtube.yt_dlp")
async def test_youtube_async_search(mock_ytdlp, mock_transcript_api_cls):
    """Async search delegates to sync via to_thread."""
    mock_ydl = MagicMock()
    mock_ydl.extract_info.return_value = MOCK_EXTRACT_INFO
    mock_ytdlp.YoutubeDL.return_value.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ytdlp.YoutubeDL.return_value.__exit__ = MagicMock(return_value=False)

    mock_api_instance = MagicMock()
    mock_api_instance.fetch.return_value = _make_fetched_transcript(MOCK_TRANSCRIPT_SNIPPETS)
    mock_transcript_api_cls.return_value = mock_api_instance

    client = YouTubeClient()
    results = await client.asearch("rust async", max_results=2)

    assert len(results) == 2
    assert results[0].source == "youtube"
