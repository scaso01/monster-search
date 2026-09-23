"""Evidence gathering and verdict rules for fact-checking a single claim.

Two stages with a hard boundary between them. ``gather`` collects web evidence and
stores each page's full text. A reader (Claude, via the /factcheck skill) labels
every source supports / refutes / irrelevant and quotes the passage it relied on.
``verify`` accepts a label only if its quote really appears in the stored text,
then derives the verdict from the surviving labels by fixed rules. The reader
never decides the verdict, so an invented quote or an unread source cannot turn
into a TRUE.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from monster_search.clients.brave_html import BraveHtmlClient
from monster_search.clients.crawl4ai_client import Crawl4AIClient
from monster_search.clients.ddg_browser import DdgBrowserClient
from monster_search.clients.gnews import GNewsClient
from monster_search.clients import reliability
from monster_search.clients.perplexity_client import PerplexityClient
from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.models import SearchResult

# Sites that publish another AI's verdict on the claim. Citing them makes the check
# circular: isthisbs.org republishes Lenz verdicts ("POWERED BY LENZ") and showed up
# in 17 of 40 sampled real claims.
EXCLUDED_DOMAINS = ("isthisbs.org", "lenz.io")
# mediamass.net auto-generates a "death hoax, alive and well" page dated today for
# any celebrity, so it would refute a real death.
HOAX_TEMPLATE_DOMAINS = ("mediamass.net",)

DEFAULT_MAX_SOURCES = 6
# The fallbacks are scarce: Brave blocks an exit for ~36 min, sometimes after one
# query, and Perplexity runs on a free account's quota. They are spent only when the
# core engines leave the claim with fewer sources that can vote than this.
FALLBACK_BELOW = 2
MAX_TEXT_CHARS = 20_000
EXCERPT_CHARS = 500
MIN_EXCERPT_CHARS = 80
EXCERPTS_PER_SOURCE = 3
MIN_QUOTE_CHARS = 20
FETCH_TIMEOUT_S = 25
STANCES = ("supports", "refutes", "irrelevant")

_STOPWORDS = frozenset(
    "the a an of in on to for and or is was were be been are that this with by as at "
    "from it its has have had not no than which who whose into over under more most "
    "less can could would should will may might does did do about after before "
    "between during their there these those they them his her he she you your our we "
    "also only such other some any all each per via".split()
)


class NoEvidenceError(Exception):
    """No usable source was found; the claim is unchecked, never a pass."""

    def __init__(self, message: str, engines: dict[str, dict], all_failed: bool) -> None:
        super().__init__(message)
        self.engines = engines
        self.all_failed = all_failed


class VerifyInputError(ValueError):
    """Labels or evidence are malformed or do not belong together."""


def _keywords(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9][a-z0-9\-\.']{2,}", text.lower()) if w not in _STOPWORDS
    }


def domain_of(url: str) -> str:
    """Registrable domain, so en.wikipedia.org and simple.wikipedia.org count once."""
    host = (urlsplit(url).hostname or "").lower()
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in (
        "co", "com", "gov", "ac", "org", "net", "edu",
    ):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _url_key(url: str) -> str:
    s = urlsplit(url)
    return f"{(s.hostname or '').lower().removeprefix('www.')}{s.path.rstrip('/')}"


def _exclusion_reason(url: str) -> str | None:
    if domain_of(url) in EXCLUDED_DOMAINS:
        return "publishes another AI's verdict (circular)"
    if domain_of(url) in HOAX_TEMPLATE_DOMAINS:
        return "auto-generated celebrity hoax page"
    if (urlsplit(url).hostname or "").lower() == "news.google.com":
        # Google News redirect the gnews client could not resolve: no fetchable
        # page, and every such link would share one fake "google.com" domain.
        return "unresolved Google News redirect"
    return None


def _run_engines(claim: str, config: Config, per_engine: int) -> tuple[list[tuple[str, list[SearchResult]]], dict[str, dict]]:
    engines = {
        "searxng": lambda: SearXNGClient(config=config).search(claim, max_results=per_engine),
        "ddg": lambda: DdgBrowserClient(config=config).search(claim, max_results=per_engine),
        "gnews": lambda: GNewsClient(config=config).search(claim, max_results=per_engine),
    }
    def with_retry(fn):
        # A single ReadTimeout from ddg left a live claim UNCHECKED; one retry fixes that.
        try:
            return fn()
        except Exception:
            return fn()

    status: dict[str, dict] = {}
    found: list[tuple[str, list[SearchResult]]] = []
    with ThreadPoolExecutor(max_workers=len(engines)) as pool:
        futures = {name: pool.submit(with_retry, fn) for name, fn in engines.items()}
        for name, fut in futures.items():
            try:
                results = fut.result()
            except Exception as exc:  # one engine down must not sink the others; it is reported
                status[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
                continue
            status[name] = {"status": "ok", "count": len(results)}
            found.append((name, results))
    return found, status


def _perplexity_sources(claim: str, config: Config, per_engine: int) -> list[SearchResult]:
    # Only the pages Perplexity cites; its own answer is another AI's verdict.
    _, sources = PerplexityClient(config=config).search(claim)
    return sources[:per_engine]


_FALLBACKS = {
    "brave": lambda claim, config, n: BraveHtmlClient(config=config).search(claim, max_results=n),
    "perplexity": _perplexity_sources,
}


def _run_fallbacks(claim: str, config: Config, per_engine: int, found: list) -> dict[str, dict]:
    status: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=len(_FALLBACKS)) as pool:
        futures = {name: pool.submit(fn, claim, config, per_engine) for name, fn in _FALLBACKS.items()}
        for name, fut in futures.items():
            try:
                results = fut.result()
            except ImportError:
                status[name] = {"status": "skipped", "error": "curl_cffi not installed"}
                continue
            except Exception as exc:  # reported in the engine status like the core engines
                status[name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
                continue
            status[name] = {"status": "ok", "count": len(results)}
            found.append((name, results))
    return status


def _counts(src: dict) -> bool:
    return src["reliability"]["rating"] not in reliability.NOT_COUNTED


def _rank(
    found: list[tuple[str, list[SearchResult]]], max_sources: int, ratings: dict[str, dict]
) -> tuple[list[dict], list[dict]]:
    merged: dict[str, dict] = {}
    excluded: dict[str, str] = {}
    for engine_idx, (engine, results) in enumerate(found):
        for rank, r in enumerate(results):
            if not r.url:
                continue
            why = _exclusion_reason(r.url)
            if why:
                excluded[r.url] = why
                continue
            key = _url_key(r.url)
            entry = merged.get(key)
            if entry is None:
                merged[key] = {
                    "title": r.title,
                    "url": r.url,
                    "domain": domain_of(r.url),
                    "snippet": html.unescape(r.snippet or ""),
                    "found_by": [engine],
                    "reliability": reliability.rate(r.url, ratings),
                    "_order": (rank, engine_idx),
                }
            elif engine not in entry["found_by"]:
                entry["found_by"].append(engine)
                entry["_order"] = min(entry["_order"], (rank, engine_idx))
    # Sites that cannot vote (social posts, fact-failing sites) only fill leftover
    # slots. Then multi-engine hits first, then alternate engines by rank so one
    # engine's long tail cannot crowd out another engine's top result.
    ranked = sorted(merged.values(), key=lambda e: (not _counts(e), -len(e["found_by"]), e["_order"]))
    for e in ranked:
        del e["_order"]
    return ranked[:max_sources], [{"url": u, "why": w} for u, w in sorted(excluded.items())]


def clean_markdown(markdown: str) -> str:
    """Page markdown reduced to readable prose: link targets and images removed."""
    # A link target is a URL (escaped parens allowed) plus an optional "title".
    target = r'\((?:\\.|[^\s()\\])*(?:\s+"[^"]*")?\)'
    text = re.sub(r"\[\[[^\]]*\]\]" + target, "", markdown)  # footnote markers like [[a]](#cite)
    text = re.sub(r"!\[[^\]]*\]" + target, " ", text)
    text = re.sub(r"\[([^\]]*)\]" + target, r"\1", text)
    text = re.sub(r"<https?://[^>]+>", " ", text)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())


def _fetch_text(url: str, config: Config) -> tuple[str, str | None]:
    try:
        markdown, _ = Crawl4AIClient(config=config).search(url, timeout=FETCH_TIMEOUT_S)
    except Exception as exc:  # recorded on the source and shown to the reader
        return "", f"{type(exc).__name__}: {exc}"[:200]
    text = clean_markdown(markdown)
    if not text.strip():
        return "", "page returned no text"
    return text[:MAX_TEXT_CHARS], None


def _chunks(text: str) -> list[str]:
    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        while len(para) > EXCERPT_CHARS:
            cut = para.rfind(" ", 0, EXCERPT_CHARS)
            cut = cut if cut > EXCERPT_CHARS // 2 else EXCERPT_CHARS
            out.append(para[:cut].strip())
            para = para[cut:].strip()
        if len(para) >= MIN_EXCERPT_CHARS:  # shorter lines are nav, headings, captions
            out.append(para)
    return out


def excerpts_for(claim: str, text: str) -> list[str]:
    """The passages most likely to bear on the claim, in document order."""
    chunks = _chunks(text)
    if not chunks:
        return [text[:EXCERPT_CHARS]] if text.strip() else []
    kw = _keywords(claim)
    scored = [(len(kw & _keywords(c)), i) for i, c in enumerate(chunks)]
    best = sorted(scored, key=lambda s: (-s[0], s[1]))[:EXCERPTS_PER_SOURCE]
    picked = sorted(i for score, i in best if score > 0) or [0]
    return [chunks[i] for i in picked]


def gather(
    claim: str,
    config: Config | None = None,
    *,
    max_sources: int = DEFAULT_MAX_SOURCES,
    fetch_pages: bool = True,
) -> dict:
    """Collect evidence for one claim. Raises NoEvidenceError when nothing usable is found."""
    config = config or Config()
    claim = claim.strip()
    if not claim:
        raise ValueError("claim is empty")

    try:
        ratings, rating_status = reliability.load()
    except reliability.ReliabilityUnavailable as exc:
        # Every source then reads "unrated": votes still count, but no majority
        # verdict can fire because none has a reliable anchor.
        ratings, rating_status = {}, {"status": "error", "error": str(exc)}

    per_engine = max(5, max_sources)
    found, engines = _run_engines(claim, config, per_engine)
    sources, excluded = _rank(found, max_sources, ratings)
    if sum(map(_counts, sources)) < FALLBACK_BELOW:
        engines.update(_run_fallbacks(claim, config, per_engine, found))
        sources, excluded = _rank(found, max_sources, ratings)
    else:
        engines.update({name: {"status": "not needed"} for name in _FALLBACKS})
    if not sources:
        all_failed = not any(s["status"] == "ok" for s in engines.values())
        why = "every search engine failed" if all_failed else "search engines returned no usable sources"
        raise NoEvidenceError(f"no evidence found: {why}", engines, all_failed)

    if fetch_pages:
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            fetched = list(pool.map(lambda s: _fetch_text(s["url"], config), sources))
    else:
        fetched = [("", "page fetch disabled")] * len(sources)

    for n, (src, (text, err)) in enumerate(zip(sources, fetched), start=1):
        src["n"] = n
        src["fetch_error"] = err
        if text:
            src["text_source"] = "page"
            src["text"] = text
        else:
            src["text_source"] = "snippet"
            src["text"] = src["snippet"]
        src["excerpts"] = excerpts_for(claim, src["text"])

    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    evidence_id = hashlib.sha1(f"{claim}|{created}|{time.time_ns()}".encode()).hexdigest()[:12]
    return {
        "evidence_id": evidence_id,
        "claim": claim,
        "created": created,
        "engines": engines,
        "reliability": rating_status,
        "excluded": excluded,
        "sources": sources,
    }


def default_evidence_path(evidence_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "monster-search-factcheck" / f"{evidence_id}.json"


def save_evidence(evidence: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def reader_view(evidence: dict) -> dict:
    """What the reader sees: everything except the full page text."""
    view = {k: v for k, v in evidence.items() if k != "sources"}
    view["sources"] = [{k: v for k, v in s.items() if k != "text"} for s in evidence["sources"]]
    return view


_QUOTE_CHARS = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                              "–": "-", "—": "-", " ": " "})


def _normalize(text: str) -> str:
    text = text.translate(_QUOTE_CHARS)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # markdown links -> link text
    text = re.sub(r"[*_`#>|]", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    """True when every fragment of the quote (split on ellipses) appears in order."""
    fragments = [f for f in (_normalize(p) for p in re.split(r"\.\.\.|…", quote)) if f]
    if sum(len(f) for f in fragments) < MIN_QUOTE_CHARS:
        return False
    haystack = _normalize(text)
    pos = 0
    for frag in fragments:
        at = haystack.find(frag, pos)
        if at < 0:
            return False
        pos = at + len(frag)
    return True


def _votes(entries: list[dict]) -> dict[str, str]:
    """{domain: rating} for counted entries; one vote per domain."""
    return {e["domain"]: e["reliability"] for e in entries if e["counted"]}


def _outweighs(side: dict[str, str], other: dict[str, str]) -> bool:
    return (
        len(side) >= 3
        and len(other) == 1
        and reliability.RELIABLE in side.values()
        and reliability.RELIABLE not in other.values()
    )


def verify(evidence: dict, labels_doc: dict) -> dict:
    """Check the reader's quotes against stored text and apply the verdict rules.

    Only counted domains vote (see reliability.NOT_COUNTED). TRUE needs 2+ supporting
    domains and none refuting, or 3+ supporting against a single refuting domain when
    a supporter is rated reliable and the dissenter is not. FALSE mirrors it; anything
    else is INCONCLUSIVE.
    """
    if labels_doc.get("evidence_id") != evidence.get("evidence_id"):
        raise VerifyInputError(
            f"labels are for evidence {labels_doc.get('evidence_id')!r}, "
            f"not {evidence.get('evidence_id')!r}"
        )
    labels = labels_doc.get("labels")
    if not isinstance(labels, list) or not labels:
        raise VerifyInputError("labels must be a non-empty list")
    by_n = {s["n"]: s for s in evidence["sources"]}
    unlabeled = set(by_n)

    supporting: list[dict] = []
    refuting: list[dict] = []
    rejected: list[dict] = []
    for lab in labels:
        n, stance, quote = lab.get("source"), lab.get("stance"), (lab.get("quote") or "")
        if n not in by_n:
            raise VerifyInputError(f"label refers to unknown source {n!r}")
        if stance not in STANCES:
            raise VerifyInputError(f"source {n}: stance must be one of {STANCES}, got {stance!r}")
        unlabeled.discard(n)
        if stance == "irrelevant":
            continue
        src = by_n[n]
        if not quote_in_text(quote, src["text"]):
            why = "quote too short" if len(_normalize(quote)) < MIN_QUOTE_CHARS else "quote not found in source text"
            rejected.append({"source": n, "stance": stance, "quote": quote, "why": why})
            continue
        rating = src.get("reliability") or {"rating": reliability.UNRATED, "why": "not rated"}
        entry = {"source": n, "domain": src["domain"], "url": src["url"], "quote": quote,
                 "reliability": rating["rating"], "reliability_why": rating["why"],
                 "counted": rating["rating"] not in reliability.NOT_COUNTED}
        (supporting if stance == "supports" else refuting).append(entry)

    if unlabeled:
        raise VerifyInputError(f"every source must be labeled; missing {sorted(unlabeled)}")

    sup = _votes(supporting)
    ref = _votes(refuting)
    dropped = sum(not e["counted"] for e in supporting + refuting)
    if len(sup) >= 2 and not ref:
        verdict, reason = "TRUE", f"supported by {len(sup)} independent sources, none against"
    elif len(ref) >= 2 and not sup:
        verdict, reason = "FALSE", f"refuted by {len(ref)} independent sources, none in support"
    elif _outweighs(sup, ref):
        verdict, reason = "TRUE", (f"supported by {len(sup)} independent sources including a reliable one; "
                                   f"1 dissent ({next(iter(ref))}, rated {ref[next(iter(ref))]})")
    elif _outweighs(ref, sup):
        verdict, reason = "FALSE", (f"refuted by {len(ref)} independent sources including a reliable one; "
                                    f"1 dissent ({next(iter(sup))}, rated {sup[next(iter(sup))]})")
    elif sup and ref:
        verdict, reason = "INCONCLUSIVE", "sources conflict"
    elif sup or ref:
        verdict, reason = "INCONCLUSIVE", "only one independent source takes a side"
    else:
        verdict, reason = "INCONCLUSIVE", "no verified evidence either way"
    if dropped:
        reason += f" ({dropped} low-reliability quote(s) not counted)"

    return {
        "evidence_id": evidence["evidence_id"],
        "claim": evidence["claim"],
        "verdict": verdict,
        "reason": reason,
        "supporting": supporting,
        "refuting": refuting,
        "rejected_quotes": rejected,
        "engines": evidence["engines"],
    }
