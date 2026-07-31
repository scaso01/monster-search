"""YouTube search + transcript client via yt-dlp and youtube-transcript-api."""

from __future__ import annotations

import asyncio

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from monster_search.config import Config
from monster_search.models import SearchResult


class YouTubeClient:
    """Search YouTube via yt-dlp, extract transcripts via youtube-transcript-api."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or Config()

    def _format_date(self, upload_date: str) -> str:
        """Convert '20240115' to '2024-01-15'."""
        if upload_date and len(upload_date) == 8:
            return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        return upload_date or ""

    def _get_transcript(self, video_id: str, max_chars: int) -> str:
        """Extract transcript text, truncated to max_chars. Returns '' on failure."""
        try:
            transcript_api = YouTubeTranscriptApi()
            fetched = transcript_api.fetch(video_id)
            text = " ".join(seg.text for seg in fetched)
            if len(text) > max_chars:
                return text[:max_chars] + "..."
            return text
        except Exception:
            return ""

    def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Synchronous YouTube search with transcript extraction."""
        max_results = max_results or self._config.max_results
        max_chars = self._config.youtube_max_transcript_chars

        opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": self._config.youtube_timeout,
        }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        except Exception as exc:
            raise RuntimeError(f"YouTube search failed: {exc}") from exc

        entries = (info or {}).get("entries") or []
        results: list[SearchResult] = []

        for entry in entries[:max_results]:
            video_id = entry.get("id", "")
            channel = entry.get("channel") or entry.get("uploader") or ""
            title = entry.get("title", "")
            if channel:
                title = f"{title} [channel: {channel}]"

            transcript = self._get_transcript(video_id, max_chars)
            snippet = transcript or entry.get("description") or ""

            results.append(SearchResult(
                title=title,
                url=f"https://www.youtube.com/watch?v={video_id}",
                snippet=snippet,
                source="youtube",
                published=self._format_date(entry.get("upload_date", "")),
                category="video",
            ))

        return results

    async def asearch(
        self,
        query: str,
        *,
        max_results: int | None = None,
    ) -> list[SearchResult]:
        """Async YouTube search (wraps sync in thread)."""
        return await asyncio.to_thread(self.search, query, max_results=max_results)
