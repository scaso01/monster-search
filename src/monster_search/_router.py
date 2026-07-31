"""Query router — regex-based classification to recommend engines."""

from __future__ import annotations

import re
from enum import Enum


class QueryCategory(Enum):
    SECURITY = "security"
    CODE = "code"
    ACADEMIC = "academic"
    NEWS = "news"
    PACKAGE = "package"
    WHOIS = "whois"
    ARCHIVE = "archive"
    VIDEO = "video"
    AI_ML = "ai_ml"
    SHOPPING = "shopping"
    DEEP_RESEARCH = "research"
    GENERAL = "general"


# Ordered by specificity (most specific first).
# Each entry: (compiled regex, category)
_PATTERNS: list[tuple[re.Pattern[str], QueryCategory]] = [
    # SECURITY — CVEs, GHSAs, vulnerability keywords
    (re.compile(
        r"CVE-\d+|GHSA-[\w-]+|CWE-\d+|vulnerabilit|exploit|malware|ransomware",
        re.IGNORECASE,
    ), QueryCategory.SECURITY),

    # WHOIS — domain patterns, IP addresses
    (re.compile(
        r"\b(?:[\w-]+\.(?:com|org|net|io|dev|co|info|biz|me|app|xyz)\b)"
        r"|(?:\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b)"
        r"|whois\b",
        re.IGNORECASE,
    ), QueryCategory.WHOIS),

    # PACKAGE — explicit ecosystem prefixes
    (re.compile(
        r"\b(?:npm|pypi|cargo|crate|gem|nuget|maven|pip):",
        re.IGNORECASE,
    ), QueryCategory.PACKAGE),

    # CODE — programming constructs
    # Matches:
    #   - leading code keywords: def/class/import/func/struct/async fn/impl/interface
    #   - source-code filename extensions: .py/.rs/.go/.js/.ts/.java/.cpp/.c/.rb
    #   - dotted.PascalCase access (e.g. asyncio.Queue, httpx.AsyncClient)
    #   - function-call syntax with snake_case/camelCase identifier (e.g. put_nowait(...))
    #   - literal "source code"
    (re.compile(
        r"\b(?:func |def |class |import |struct |async fn |impl |interface )"
        r"|\.(?:py|rs|go|js|ts|java|cpp|c|rb)\b"
        r"|\b[a-z_][a-zA-Z0-9_]*\.[A-Z][a-zA-Z0-9_]*"
        r"|\b[a-z_][a-zA-Z0-9_]{2,}\([^)]*\)"
        r"|\bsource\s*code\b",
    ), QueryCategory.CODE),

    # ACADEMIC — research/paper keywords.
    # Requires an unambiguous academic term.  Year alone, "vs" alone, or a bare
    # comparison query must NOT trigger this category — they route to GENERAL.
    # "research" alone (without "paper") is included so that "research methodology"
    # and "climate research 2026 IPCC" correctly reach academic engines.
    (re.compile(
        r"\b(?:paper|research|study|journal|arxiv|doi:|preprint|thesis"
        r"|peer[\s-]?review|citation|abstract)\b",
        re.IGNORECASE,
    ), QueryCategory.ACADEMIC),

    # NEWS — recency keywords
    (re.compile(
        r"\b(?:latest|breaking|today|yesterday|this\s+week|this\s+month"
        r"|headlines|current\s+events|news)\b",
        re.IGNORECASE,
    ), QueryCategory.NEWS),

    # ARCHIVE — historical/cached content
    (re.compile(
        r"\b(?:wayback|archive|cached|historical|snapshot|internet\s+archive)\b",
        re.IGNORECASE,
    ), QueryCategory.ARCHIVE),

    # VIDEO — tutorial/howto/video keywords
    (re.compile(
        r"\b(?:tutorial|how\s+to|walkthrough|demo|video|youtube"
        r"|screencast|watch)\b",
        re.IGNORECASE,
    ), QueryCategory.VIDEO),

    # AI_ML — ML/AI model keywords (specific to avoid false positives on bare "model")
    (re.compile(
        r"\b(?:huggingface|fine[\s-]?tun\w*|(?:llm|lora|gguf)\b"
        r"|pretrained|diffusion\s+model)\b",
        re.IGNORECASE,
    ), QueryCategory.AI_ML),

    # SHOPPING — commerce/buy/price keywords + dollar amounts.
    # Deliberately NARROW: the prose-ambiguous words "deal(s)", "discount", and
    # "retail" were dropped — they fired on non-commercial queries ("deal with X",
    # "the price of fame" keeps "price" but those are rarer). Anything dropped here
    # falls through to GENERAL, where searxng still surfaces product results; the
    # gated deal engines (Slickdeals/Newegg) are a bonus layer, not the only source.
    (re.compile(
        r"\b(?:buy|price|cheap(?:est)?|for\s+sale|in\s+stock|where\s+to\s+buy"
        r"|best\s+price|compare\s+prices?|under\s+\$|affordable|coupon)\b"
        r"|\$\d+",
        re.IGNORECASE,
    ), QueryCategory.SHOPPING),

    # DEEP_RESEARCH — complex analysis requests
    (re.compile(
        r"\b(?:compare|analysis|deep\s+dive|explain\s+in\s+detail"
        r"|comprehensive|thorough|in[\s-]?depth|survey|overview)\b",
        re.IGNORECASE,
    ), QueryCategory.DEEP_RESEARCH),
]


# Category → engine names
_CATEGORY_ENGINES: dict[QueryCategory, list[str]] = {
    QueryCategory.SECURITY: ["osv", "searxng"],
    QueryCategory.CODE: ["zoekt", "grepapp", "github_code", "searxng"],
    QueryCategory.ACADEMIC: ["arxiv", "semantic_scholar", "openalex"],
    QueryCategory.NEWS: ["news", "gnews", "hackernews", "searxng"],
    QueryCategory.PACKAGE: ["deps", "searxng"],
    QueryCategory.WHOIS: ["whodat"],
    QueryCategory.ARCHIVE: ["archive_org", "searxng"],
    QueryCategory.VIDEO: ["youtube", "searxng"],
    QueryCategory.AI_ML: ["huggingface", "searxng"],
    QueryCategory.SHOPPING: [
        "searxng_shopping", "slickdeals", "cheapshark", "deals_rss",
        "priceghost", "amazon_deals", "newegg", "searxng",
    ],
    # NOTE: local_researcher is tier-3 (3-8 min) and was hanging smart-tiered
    # runs past the 180s timeout. Vane/Khoj are tier-2 AI engines that complete
    # in ~2 min and produce comparable analysis. Use --deep --engine local_researcher
    # explicitly when you need the iterative research loop.
    QueryCategory.DEEP_RESEARCH: [
        "vane", "khoj", "searxng",
    ],
    QueryCategory.GENERAL: ["searxng", "marginalia", "mwmbl", "perplexity", "hackernews", "reddit"],
}


def classify_query(query: str) -> QueryCategory:
    """Classify a query into a category using regex patterns.

    Patterns are checked in specificity order. First match wins, which
    handles mixed signals (e.g. "latest CVE" matches SECURITY before NEWS).
    Empty/whitespace queries default to GENERAL.
    """
    if not query or not query.strip():
        return QueryCategory.GENERAL

    for pattern, category in _PATTERNS:
        if pattern.search(query):
            return category

    return QueryCategory.GENERAL


def get_engines_for_category(category: QueryCategory) -> list[str]:
    """Return engine names for a given category."""
    return list(_CATEGORY_ENGINES[category])


def route_query(query: str) -> list[str]:
    """Classify query and return recommended engines."""
    category = classify_query(query)
    return get_engines_for_category(category)
