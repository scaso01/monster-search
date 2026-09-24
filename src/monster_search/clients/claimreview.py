"""Published fact-checks (schema.org ClaimReview) from the Data Commons feed.

One free file, no key: ~100k fact-checks from PolitiFact, FactCheck.org, AFP, dpa and
others, each with the claim as the fact-checker worded it and their rating. It is
indexed locally (SQLite FTS5) and refreshed weekly. A match becomes an ordinary
source — the fact-check page, rated and quote-checked like any other — never a
verdict on its own.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

import httpx

from monster_search.models import SearchResult

FEED_URL = "https://storage.googleapis.com/datacommons-feeds/claimreview/latest/data.json"
CACHE_DIR = Path.home() / ".cache" / "monster-search"
MAX_AGE_S = 7 * 24 * 3600
DOWNLOAD_TIMEOUT_S = 300
# A fact-check must share at least this share of the claim's keywords, so a PolitiFact
# page about a different Biden quote is not offered as evidence for this one.
MIN_OVERLAP = 0.6
MIN_ITEMS = 50_000  # a feed far smaller than ~100k means a broken download, not fewer checks

_STOPWORDS = frozenset(
    "the a an of in on to for and or is was were be been are that this with by as at "
    "from it its has have had not no than which who into over under more most can "
    "could would should will may might does did do about after before their there "
    "these those they them his her he she you your our we also only such some any all".split()
)


class ClaimReviewUnavailable(RuntimeError):
    """No index exists and the feed could not be downloaded."""


def _db_path() -> Path:
    return CACHE_DIR / "claimreview.db"


def keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9']{2,}", text.lower()) if w not in _STOPWORDS}


def _items(feed: dict):
    for element in feed.get("dataFeedElement") or []:
        for item in element.get("item") or []:
            claim = (item.get("claimReviewed") or "").strip()
            url = item.get("url") or ""
            if claim and url.startswith("http"):
                yield (
                    re.sub(r"\s+", " ", claim),
                    url,
                    ((item.get("author") or {}).get("name") or "").strip(),
                    ((item.get("reviewRating") or {}).get("alternateName") or "").strip(),
                    (item.get("datePublished") or "")[:10],
                )


def build_index(feed: dict, db_path: Path) -> int:
    rows = list(_items(feed))
    if len(rows) < MIN_ITEMS:
        raise ClaimReviewUnavailable(f"feed has only {len(rows)} usable fact-checks; download looks broken")
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    with con:
        con.execute("CREATE VIRTUAL TABLE checks USING fts5(claim, url UNINDEXED, publisher UNINDEXED, "
                    "rating UNINDEXED, published UNINDEXED)")
        con.executemany("INSERT INTO checks VALUES (?,?,?,?,?)", rows)
        con.execute("CREATE TABLE meta (built REAL)")
        con.execute("INSERT INTO meta VALUES (?)", (time.time(),))
    con.close()
    tmp.replace(db_path)
    return len(rows)


def refresh(db_path: Path | None = None) -> int:
    """Download the feed and rebuild the index; number of fact-checks indexed."""
    db_path = db_path or _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    resp = httpx.get(FEED_URL, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True)
    resp.raise_for_status()
    return build_index(json.loads(resp.content), db_path)


def _built(db_path: Path) -> float:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT built FROM meta").fetchone()[0]
    finally:
        con.close()


def ensure_index(db_path: Path | None = None) -> Path:
    """The index, rebuilt when a week old; a stale index beats none."""
    db_path = db_path or _db_path()
    if db_path.exists() and time.time() - _built(db_path) < MAX_AGE_S:
        return db_path
    try:
        refresh(db_path)
    except Exception as exc:  # stale index still answers; with none, the caller reports the engine down
        if db_path.exists():
            return db_path
        raise ClaimReviewUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc
    return db_path


class ClaimReviewClient:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    def search(self, claim: str, max_results: int = 3) -> list[SearchResult]:
        kw = keywords(claim)
        if not kw:
            return []
        con = sqlite3.connect(ensure_index(self._db_path))
        try:
            rows = con.execute(
                "SELECT claim, url, publisher, rating, published FROM checks WHERE checks MATCH ? "
                "ORDER BY bm25(checks) LIMIT 50",
                (" OR ".join(f'"{w}"' for w in sorted(kw)),),
            ).fetchall()
        finally:
            con.close()
        results = []
        for text, url, publisher, rating, published in rows:
            if len(kw & keywords(text)) / len(kw) < MIN_OVERLAP:
                continue
            results.append(SearchResult(
                title=f"Fact-check by {publisher or 'unknown'}: {text[:120]}",
                url=url,
                # Both fields are the fact-checker's own published markup, so this
                # stands in for the page when the page itself cannot be fetched.
                snippet=f'Claim reviewed: "{text}" Rating: {rating or "none given"}.',
                source="claimreview",
                engine="claimreview",
                published=published or None,
            ))
            if len(results) >= max_results:
                break
        return results
