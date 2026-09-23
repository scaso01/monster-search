"""Site reliability ratings for fact-check sources.

Two free lists, merged by domain:
- Wikipedia's Perennial Sources (WP:RSP): community consensus per publication,
  rated generally reliable / no consensus / generally unreliable / deprecated.
- Iffy.news: low-credibility sites, used only where the listing is fact-based
  (Media Bias/Fact Check factual grade Low/Very Low, or Wikipedia's fake-news list).

A domain on several rows (Fox News is rated per topic) keeps its worst rating, so a
site never counts as reliable on the strength of its best section.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CACHE_PATH = Path.home() / ".cache" / "monster-search" / "reliability.json"
MAX_AGE_S = 7 * 24 * 3600
TIMEOUT_S = 30
_UA = {"User-Agent": "monster-search/0.1 (personal fact-check tool; https://github.com/scaso01/monster-search)"}

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_RSP_PREFIX = "Reliable sources/Perennial sources/"
_IFFY_CSV = (
    "https://docs.google.com/spreadsheets/d/1ck1_FZC-97uDLIlvRJDTrGqBk0FuDe9yHkluROgpGS8"
    "/gviz/tq?tqx=out:csv&sheet=Iffy-news"
)

RELIABLE = "reliable"
UNRATED = "unrated"
# Worst first. Only the first three are dropped as votes: each is defined by failed
# fact checks or by who wrote it, not by a publication's politics. "unreliable"
# (Wikipedia's generally-unreliable, which rates Fox News that way on politics and
# is itself accused of a lean) still counts, but cannot anchor a majority verdict.
ORDER = ("deprecated", "low-credibility", "user-generated", "unreliable",
         "no consensus", UNRATED, RELIABLE)
NOT_COUNTED = ("deprecated", "low-credibility", "user-generated")
_RSP_CODES = {"gr": RELIABLE, "nc": "no consensus", "m": "no consensus",
              "gu": "unreliable", "d": "deprecated", "b": "deprecated"}

# Wikipedia rates itself unreliable only as a citation inside Wikipedia (circular
# sourcing); as evidence for a reader it is an ordinary, unrated source.
OVERRIDES = {"wikipedia.org": (UNRATED, "Wikipedia's self-rating applies only within Wikipedia")}
# Posts by anyone: a viral post repeating a claim is not evidence for it.
_UGC = ("facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
        "youtube.com", "reddit.com", "quora.com", "pinterest.com", "threads.net",
        "brainly.in", "brainly.com", "medium.com", "substack.com", "linkedin.com")
OVERRIDES.update({d: ("user-generated", "user-generated posts") for d in _UGC})


class ReliabilityUnavailable(RuntimeError):
    """Neither a fresh download nor a cached copy of the lists is available."""


def _worse(a: str, b: str) -> str:
    return a if ORDER.index(a) <= ORDER.index(b) else b


def _put(table: dict[str, dict], domain: str, rating: str, why: str) -> None:
    domain = domain.strip().lower().removeprefix("www.").strip("/")
    if not domain or "." not in domain or " " in domain:
        return
    old = table.get(domain)
    if old is None or _worse(rating, old["rating"]) == rating and rating != old["rating"]:
        table[domain] = {"rating": rating, "why": why}


def parse_rsp(wikitext: str, table: dict[str, dict]) -> None:
    for row in wikitext.split("\n|-"):
        codes = re.findall(r"\{\{WP:RSPSTATUS\|([a-z]+)", row)
        uses = re.search(r"\{\{WP:RSPUSES\|([^}]*)\}\}", row)
        if not codes or not uses:
            continue
        ratings = [_RSP_CODES[c] for c in codes if c in _RSP_CODES]
        if not ratings:
            continue
        rating = ratings[0]
        for r in ratings[1:]:
            rating = _worse(rating, r)
        name = re.search(r"\n\|\s*\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", row)
        why = f"Wikipedia RSP: {rating}" + (f" ({name.group(1)})" if name else "")
        for domain in uses.group(1).split("|"):
            if "=" not in domain:
                _put(table, domain, rating, why)


def parse_iffy(csv_text: str, table: dict[str, dict]) -> None:
    # Only fact-based listings: a Low/Very Low factual grade or Wikipedia's fake-news
    # list. A "Mixed" grade alone would list Fox News while CNN counts, which is a
    # judgement about slant, not about publishing false facts.
    for row in csv.DictReader(io.StringIO(csv_text)):
        fact, bias = row.get("MBFC Fact", "").strip(), row.get("MBFC Bias", "").strip()
        if fact not in ("L", "VL") and not row.get("Wiki Fake", "").strip():
            continue
        detail = ", ".join(p for p in (fact and f"MBFC fact {fact}", bias and f"bias {bias}") if p)
        _put(table, row.get("Domain", ""), "low-credibility",
             "Iffy.news low-credibility list" + (f" ({detail})" if detail else ""))


def _wiki_json(client: httpx.Client, params: dict) -> dict:
    # Wikimedia answers bursts with 429 + Retry-After (seen: 20s); honour it once.
    for attempt in range(2):
        resp = client.get(_WIKI_API, params=params)
        if resp.status_code == 429 and attempt == 0:
            time.sleep(min(int(resp.headers.get("retry-after", "20")), 60))
            continue
        resp.raise_for_status()
        return resp.json()
    raise ReliabilityUnavailable("Wikipedia kept answering 429")


def _fetch_rsp(client: httpx.Client) -> list[str]:
    pages = _wiki_json(client, {
        "action": "query", "list": "allpages", "apnamespace": 4,
        "apprefix": _RSP_PREFIX, "aplimit": 100, "format": "json",
    })["query"]["allpages"]
    numbered = [p["title"] for p in pages if p["title"].rsplit("/", 1)[-1].isdigit()]
    if not numbered:
        raise ReliabilityUnavailable("Wikipedia returned no Perennial Sources subpages")
    texts = []
    for title in numbered:
        time.sleep(1)
        data = _wiki_json(client, {
            "action": "parse", "page": title, "prop": "wikitext",
            "format": "json", "formatversion": 2,
        })
        texts.append(data["parse"]["wikitext"])
    return texts


def download() -> dict[str, dict]:
    table: dict[str, dict] = {}
    with httpx.Client(timeout=TIMEOUT_S, headers=_UA, follow_redirects=True) as client:
        for text in _fetch_rsp(client):
            parse_rsp(text, table)
        resp = client.get(_IFFY_CSV)
        resp.raise_for_status()
        parse_iffy(resp.text, table)
    # A list that suddenly shrinks means a format change, not a cleaner web.
    if len(table) < 1000:
        raise ReliabilityUnavailable(f"only {len(table)} domains parsed; a list format changed")
    return table


def load(cache_path: Path = CACHE_PATH) -> tuple[dict[str, dict], dict]:
    """(table, status). Refreshes a week-old cache; a stale cache beats nothing."""
    cached = None
    if cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        age = time.time() - cached["fetched"]
        if age < MAX_AGE_S:
            return cached["domains"], {"status": "ok", "domains": len(cached["domains"])}
    try:
        table = download()
    except Exception as exc:  # the stale cache or the caller's "unavailable" path takes over
        if cached:
            return cached["domains"], {"status": "stale", "domains": len(cached["domains"]),
                                       "error": f"{type(exc).__name__}: {exc}"[:200]}
        raise ReliabilityUnavailable(f"{type(exc).__name__}: {exc}"[:300]) from exc
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"fetched": time.time(), "domains": table}), encoding="utf-8")
    return table, {"status": "ok", "domains": len(table)}


def rate(url: str, table: dict[str, dict]) -> dict:
    """Rating for a URL: the most specific listed host wins (abcnews.go.com before go.com)."""
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in OVERRIDES:
            rating, why = OVERRIDES[candidate]
            return {"rating": rating, "why": why}
        if candidate in table:
            return dict(table[candidate])
    return {"rating": UNRATED, "why": "not on either list"}
