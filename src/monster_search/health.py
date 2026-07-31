"""Health checks for search containers and external APIs.

PROBE PHILOSOPHY
----------------
Every probe issues a **real, known-good query** and verifies the response
contains ≥1 result (or a structurally-valid empty response where 0 is
legitimately correct).  "Endpoint responds with 2xx" is NOT sufficient —
that pattern produced false positives (broken engines) and false negatives
(working engines blocked by Cloudflare/UA) in v0.10.0.

Latency budget
--------------
All probes run in parallel via concurrent.futures.ThreadPoolExecutor.
Slow engines (perplexity, vane, khoj, fyin) get startup/config checks only
— issuing a real query would blow the 60s wall-clock budget.  These are
documented explicitly in their probe docstrings.

Health dict values
------------------
True  → UP (produced ≥1 result or confirmed configured + reachable)
False → DOWN (connection refused, wrong status, 0 results, missing key, etc.)

The reason for DOWN is surfaced in the health_reasons dict that
check_health() also returns as a second value.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable
from urllib.parse import quote

import httpx

from monster_search.clients.archive_org import (
    _advanced_search_url,
    _build_advanced_ssh_command,
)
from monster_search.config import Config

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client(timeout: float = 10, **kwargs) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True, **kwargs)


# ---------------------------------------------------------------------------
# Individual probes
# Each probe returns (bool, str) — (is_up, reason).
# Reason is empty string on success; describes the failure on False.
# ---------------------------------------------------------------------------


def _probe_searxng(config: Config) -> tuple[bool, str]:
    """Issue a real query and require ≥1 result."""
    try:
        with _client(timeout=10) as c:
            resp = c.get(
                f"{config.searxng_url}/search",
                params={"q": "tokio rust", "format": "json"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return False, "0 results for probe query 'tokio rust'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_local_researcher(config: Config) -> tuple[bool, str]:
    """Heartbeat only — /ok endpoint.  A real research query takes 3-8 min."""
    try:
        with _client(timeout=5) as c:
            resp = c.get(f"{config.local_researcher_url}/ok")
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_crawl4ai(config: Config) -> tuple[bool, str]:
    """Heartbeat only — /health endpoint.  Crawl4AI is a utility, not a query engine."""
    try:
        with _client(timeout=5) as c:
            resp = c.get(f"{config.crawl4ai_url}/health")
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_whodat(config: Config) -> tuple[bool, str]:
    """Look up 'example.com' and require a non-empty JSON body."""
    try:
        with _client(timeout=8) as c:
            resp = c.get(f"{config.whodat_url}/example.com")
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        body = resp.text.strip()
        if not body or body in ("null", "{}"):
            return False, "empty WHOIS response for example.com"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_zoekt(config: Config) -> tuple[bool, str]:
    """Search for 'fn' and require at least one hit.

    A Zoekt instance only knows the repositories you have indexed into it, so
    a probe query has to be something that matches almost any source tree
    rather than a specific project. 'fn' appears in Rust and Python alike.
    A zero-result answer here usually means an empty index, not a dead server.

    NOTE: The Zoekt API wraps results under {"Result": {"Files": [...]}},
    NOT at the top level.
    """
    try:
        with _client(timeout=8) as c:
            resp = c.post(
                f"{config.zoekt_url}/api/search",
                json={"Q": "fn", "Opts": {"MaxDocDisplayCount": 1}},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        files = (resp.json().get("Result") or {}).get("Files") or []
        if not files:
            return False, "0 results for probe query 'fn' — index may be empty"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_changedetection(config: Config) -> tuple[bool, str]:
    """System info check — changedetection.io is a utility service, not a query engine."""
    if not config.changedetection_api_key:
        return False, "no MONSTER_CHANGEDETECTION_API_KEY set"
    try:
        with _client(timeout=5) as c:
            resp = c.get(
                f"{config.changedetection_url}/api/v1/systeminfo",
                headers={"x-api-key": config.changedetection_api_key},
            )
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_vane(config: Config) -> tuple[bool, str]:
    """Provider list check — a real Vane search takes ~2 min (over health budget).

    This probe confirms Vane is up and has at least one provider configured.
    """
    try:
        with _client(timeout=8) as c:
            resp = c.get(f"{config.vane_url}/api/providers")
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        # Providers endpoint returns a list or dict; any non-empty body is OK
        body = resp.json()
        if not body:
            return False, "providers list is empty"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_khoj(config: Config) -> tuple[bool, str]:
    """Health endpoint check — a real Khoj query takes ~2 min (over health budget)."""
    try:
        with _client(timeout=8) as c:
            resp = c.get(f"{config.khoj_url}/api/health")
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_meilisearch(config: Config) -> tuple[bool, str]:
    """Health endpoint — Meilisearch is a result cache, not a search engine."""
    try:
        with _client(timeout=5) as c:
            resp = c.get(f"{config.meilisearch_url}/health")
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_perplexity(config: Config) -> tuple[bool, str]:
    """Config + library check.

    A real Perplexity query takes 30-60s — over the 60s total health budget
    when run alongside all other probes.  Instead we verify:
      1. Auth is configured — EITHER a static session token OR the self-healing
         browser-cookie path (MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER).
      2. curl_cffi (required TLS fingerprinting library) is importable.

    If both auth paths are missing the engine will definitely fail at query time.
    This is explicitly *not* an end-to-end proof of token validity — a fresh
    session token check is out of scope for a cheap health probe.

    NOTE: checking only the static token reported DOWN for users on the
    browser-cookie path (the engine actually works) — hence both are accepted.
    """
    if not config.perplexity_session_token and not config.perplexity_cookies_from_browser:
        return False, (
            "no MONSTER_PERPLEXITY_SESSION_TOKEN or "
            "MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER set"
        )
    if importlib.util.find_spec("curl_cffi") is None:
        return False, "curl_cffi not installed (pip install curl_cffi)"
    return True, ""


def _probe_arxiv(_config: Config) -> tuple[bool, str]:
    """Query arXiv for 'transformer' and require ≥1 entry in Atom XML."""
    try:
        with _client(timeout=12) as c:
            resp = c.get(
                "https://export.arxiv.org/api/query",
                params={"search_query": "all:transformer", "max_results": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            return False, "0 entries in arXiv response for 'transformer'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"
    except ET.ParseError as exc:
        return False, f"XML parse error: {exc}"


def _probe_semantic_scholar(_config: Config) -> tuple[bool, str]:
    """Query Semantic Scholar for 'transformer' and require ≥1 paper.

    HTTP 429 → DOWN with informative reason (add API key to avoid rate limits).
    """
    try:
        with _client(timeout=12) as c:
            resp = c.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": "transformer", "limit": "1", "fields": "title"},
            )
        if resp.status_code == 429:
            return False, "HTTP 429 rate-limited (set MONSTER_SEMANTIC_SCHOLAR_API_KEY to avoid this)"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json().get("data", [])
        if not data:
            return False, "0 papers returned for probe query 'transformer'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_openalex(_config: Config) -> tuple[bool, str]:
    """Query OpenAlex for 'transformer' and require ≥1 result."""
    try:
        with _client(timeout=12) as c:
            resp = c.get(
                "https://api.openalex.org/works",
                params={"search": "transformer", "per_page": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        results = resp.json().get("results", [])
        if not results:
            return False, "0 results for probe query 'transformer'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_osv(_config: Config) -> tuple[bool, str]:
    """Query OSV for known jinja2 vulnerabilities and require ≥1 vuln.

    pypi:jinja2 has multiple CVEs and will always return results on a
    healthy OSV instance.
    """
    try:
        with _client(timeout=10) as c:
            resp = c.post(
                "https://api.osv.dev/v1/query",
                json={"package": {"name": "jinja2", "ecosystem": "PyPI"}},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        vulns = resp.json().get("vulns", [])
        if not vulns:
            return False, "0 vulns returned for pypi:jinja2 probe — unexpected"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_deps(_config: Config) -> tuple[bool, str]:
    """Fetch npm:express package info and verify a non-empty response."""
    try:
        with _client(timeout=10) as c:
            resp = c.get("https://api.deps.dev/v3alpha/systems/npm/packages/express")
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        # Response has "packageKey" at minimum; empty JSON object → DOWN
        if not data:
            return False, "empty response for npm:express probe"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_gnews(_config: Config) -> tuple[bool, str]:
    """Fetch Google News RSS for 'technology' and require ≥1 <item>."""
    try:
        with _client(timeout=10) as c:
            resp = c.get(
                "https://news.google.com/rss/search",
                params={"q": "technology", "hl": "en-US"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        if not items:
            return False, "0 <item> elements in Google News RSS for 'technology'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"
    except ET.ParseError as exc:
        return False, f"XML parse error: {exc}"


def _probe_marginalia(config: Config) -> tuple[bool, str]:
    """Query Marginalia for 'tokio' and require ≥1 result.

    The old probe hit the root URL (/) which returns the docs page, not
    search results — causing a false negative when the real search API
    was healthy.
    """
    try:
        with _client(timeout=15) as c:
            resp = c.get(
                f"{config.marginalia_url}/public/search/{quote('tokio')}",
                params={"count": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        results = resp.json().get("results", [])
        if not results:
            return False, "0 results for probe query 'tokio'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_mwmbl(config: Config) -> tuple[bool, str]:
    """Query Mwmbl for 'python' and require ≥1 result.

    Mwmbl's API returns a JSON list (not a {results: [...]} object), so an
    empty list means the index returned nothing for the probe query.
    """
    try:
        with _client(timeout=10) as c:
            resp = c.get(
                f"{config.mwmbl_url}/api/v1/search/",
                params={"s": "python"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        results = resp.json()
        if not isinstance(results, list) or not results:
            return False, "0 results for probe query 'python'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_archive_org(config: Config) -> tuple[bool, str]:
    """Probe archive_org over whichever route the engine would actually take.

    The engine goes out over plain HTTP unless MONSTER_SSH_HOST is set, in
    which case it curls from that host instead. The probe branches the same way
    so it can never report healthy on a route the engine does not use.

    Latency budget either way is under 5s: one request for a single result.
    """
    # Probe budget is tight — request just 1 result with a 5s total timeout.
    _PROBE_TIMEOUT = 5

    if not config.ssh_host:
        try:
            resp = httpx.get(
                _advanced_search_url("python tutorial", 1), timeout=_PROBE_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            return False, f"connection error: {exc}"
        except ValueError as exc:
            return False, f"archive_org returned non-JSON: {exc}"
    else:
        cmd = _build_advanced_ssh_command(
            "python tutorial", 1, _PROBE_TIMEOUT, config.ssh_host
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False, f"archive_org SSH probe timed out after {_PROBE_TIMEOUT}s"
        except OSError as exc:
            return False, f"SSH not available: {exc}"

        if result.returncode != 0 and not result.stdout.strip():
            return False, (
                f"archive_org SSH probe failed (exit {result.returncode}): "
                f"{result.stderr[:200]}"
            )

        try:
            data = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            return False, f"archive_org SSH probe returned non-JSON: {exc}"

    docs = (data.get("response") or {}).get("docs", [])
    if not docs:
        return False, "archive_org probe returned 0 docs for 'python tutorial'"
    return True, ""


def _probe_youtube(_config: Config) -> tuple[bool, str]:
    """Import check for yt-dlp and youtube-transcript-api.

    A real YouTube search requires network + yt-dlp subprocess and takes 5-10s.
    Library availability is the gatekeeping check — if either import fails,
    all YouTube searches will fail at runtime.
    """
    if importlib.util.find_spec("yt_dlp") is None:
        return False, "yt-dlp not installed"
    if importlib.util.find_spec("youtube_transcript_api") is None:
        return False, "youtube-transcript-api not installed"
    return True, ""


def _probe_grepapp(_config: Config) -> tuple[bool, str]:
    """Query grep.app for 'fn main' and require a successful response.

    HTTP 429 → DOWN with informative reason (VPN exit IP often rate-limited).
    """
    try:
        with _client(timeout=12) as c:
            resp = c.get(
                "https://grep.app/api/search",
                params={"q": "fn main", "regexp": "false"},
            )
        if resp.status_code == 429:
            return False, "HTTP 429 rate-limited (VPN exit IP may be blocked by grep.app)"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return False, "0 hits for probe query 'fn main'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_github_code(_config: Config) -> tuple[bool, str]:
    """Run 'gh search code fn main --limit 1' and verify ≥1 result.

    The old probe only ran 'gh auth status' (never exercised search code),
    which hid a threading crash in the gh CLI subprocess.  This probe
    exercises the actual search code path.
    """
    try:
        result = subprocess.run(
            ["gh", "search", "code", "fn main", "--json", "path,repository", "--limit", "1"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "gh search code timed out after 15s"
    except OSError as exc:
        return False, f"gh CLI not found: {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()[:300]
        return False, f"gh search code exit {result.returncode}: {stderr}"

    try:
        items = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError as exc:
        return False, f"JSON parse error on gh output: {exc}"

    if not items:
        return False, "0 results for probe query 'fn main' (gh search code)"
    return True, ""


def _probe_github_repos(_config: Config) -> tuple[bool, str]:
    """Run 'gh search repos tokio --limit 1' and verify ≥1 repo.

    The old probe only ran 'gh auth status' — masking that 'gh search repos'
    returns 0 results on broad queries due to a client bug.
    """
    try:
        result = subprocess.run(
            [
                "gh", "search", "repos", "tokio",
                "--json", "fullName,stargazersCount",
                "--limit", "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False, "gh search repos timed out after 15s"
    except OSError as exc:
        return False, f"gh CLI not found: {exc}"

    if result.returncode != 0:
        stderr = result.stderr.strip()[:300]
        return False, f"gh search repos exit {result.returncode}: {stderr}"

    try:
        items = json.loads(result.stdout) if result.stdout.strip() else []
    except json.JSONDecodeError as exc:
        return False, f"JSON parse error on gh output: {exc}"

    if not items:
        return False, "0 repos for probe query 'tokio' (gh search repos)"
    return True, ""


def _probe_hackernews(_config: Config) -> tuple[bool, str]:
    """Query Algolia HN for 'rust' and require ≥1 hit."""
    try:
        with _client(timeout=10) as c:
            resp = c.get(
                "https://hn.algolia.com/api/v1/search",
                params={"query": "rust", "hitsPerPage": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        hits = resp.json().get("hits", [])
        if not hits:
            return False, "0 hits for probe query 'rust'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_huggingface(_config: Config) -> tuple[bool, str]:
    """Query HuggingFace Hub for 'bert' and require ≥1 model."""
    try:
        with _client(timeout=12) as c:
            resp = c.get(
                "https://huggingface.co/api/models",
                params={"search": "bert", "limit": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        models = resp.json()
        if not models:
            return False, "0 models for probe query 'bert'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_reddit(config: Config) -> tuple[bool, str]:
    """Run a real one-result Reddit search and require at least one entry.

    This goes through RedditClient rather than re-issuing the request here.
    The probe used to carry its own copy of the endpoint, so when Reddit
    retired the feed on old.reddit.com the two drifted apart and the probe
    kept reporting an engine that had already stopped working.
    """
    from monster_search.clients.reddit import RedditClient

    try:
        results = RedditClient(config=config).search("rust programming", max_results=1)
    except httpx.HTTPStatusError as exc:
        # Reddit rate-limits the feed per source address after a handful of
        # requests, so repeated health checks provoke this on their own. It is
        # a distinct condition from the engine being unreachable and wants a
        # different response, namely waiting rather than debugging.
        if exc.response.status_code == 429:
            return False, "rate limited by reddit (429), try again in a few minutes"
        return False, f"HTTP {exc.response.status_code}"
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"
    except RuntimeError as exc:
        return False, str(exc)
    if not results:
        return False, "0 entries in Reddit RSS for 'rust programming'"
    return True, ""


def _probe_fyin(config: Config) -> tuple[bool, str]:
    """Check the fyin binary exists on the configured SSH host."""
    if not config.ssh_host:
        return False, "MONSTER_SSH_HOST not set — fyin is disabled"
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", config.ssh_host,
             "test", "-f", "/usr/local/bin/fyin"],
            capture_output=True,
            timeout=8,
        )
        if result.returncode == 0:
            return True, ""
        return False, f"fyin not found at /usr/local/bin/fyin on {config.ssh_host}"
    except subprocess.TimeoutExpired:
        return False, f"SSH to {config.ssh_host} timed out"
    except OSError as exc:
        return False, f"SSH not available: {exc}"



_CHEAPSHARK_UA = "monster-search/0.10.0 (health-probe)"


def _probe_cheapshark(_config: Config) -> tuple[bool, str]:
    """Query CheapShark for 'elden' deals and require ≥1 result.

    CheapShark requires no API key but DOES require a descriptive User-Agent
    header — requests with the default httpx UA receive HTTP 400.  Uses the
    /api/1.0/deals endpoint (same as CheapSharkClient) with pageSize=1.
    """
    try:
        with httpx.Client(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": _CHEAPSHARK_UA},
        ) as c:
            resp = c.get(
                "https://www.cheapshark.com/api/1.0/deals",
                params={"title": "elden", "onSale": 1, "pageSize": 1},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        results = resp.json()
        if not results:
            return False, "0 deals for probe query 'elden' on CheapShark"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


def _probe_slickdeals(_config: Config) -> tuple[bool, str]:
    """Fetch Slickdeals RSS for 'laptop' and require ≥1 item."""
    try:
        with _client(timeout=15) as c:
            resp = c.get(
                "https://slickdeals.net/newsearch.php",
                params={"mode": "frontpage", "searchtype": "4", "q": "laptop", "rss": "1"},
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")
        if not items:
            return False, "0 <item> elements in Slickdeals RSS for 'laptop'"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"
    except ET.ParseError as exc:
        return False, f"XML parse error: {exc}"


def _probe_searchcode_repo(_config: Config) -> tuple[bool, str]:
    """POST to searchcode API with a known public repo and require ≥1 match.

    Uses the httpx repo itself — a well-known Python project with plenty of
    'import httpx' occurrences.  The probe does not use a real git URL for
    privacy reasons; searchcode normalises the repo field to just the owner/name
    portion, so we pass the canonical HTTPS clone URL.
    """
    try:
        with _client(timeout=12) as c:
            resp = c.post(
                "https://api.searchcode.com/api/v1/code_search",
                params={"client": "monster-search"},
                json={
                    "repository": "https://github.com/encode/httpx",
                    "query": "import httpx",
                },
            )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        results = resp.json().get("results", [])
        if not results:
            return False, "0 results for probe query 'import httpx' in encode/httpx"
        return True, ""
    except httpx.HTTPError as exc:
        return False, f"connection error: {exc}"


# ---------------------------------------------------------------------------
# Registry: engine name → probe function
# ---------------------------------------------------------------------------

_PROBES: dict[str, Callable[[Config], tuple[bool, str]]] = {
    "searxng": _probe_searxng,
    "local_researcher": _probe_local_researcher,
    "crawl4ai": _probe_crawl4ai,
    "whodat": _probe_whodat,
    "zoekt": _probe_zoekt,
    "changedetection": _probe_changedetection,
    "vane": _probe_vane,
    "khoj": _probe_khoj,
    "meilisearch": _probe_meilisearch,
    "perplexity": _probe_perplexity,
    "arxiv": _probe_arxiv,
    "openalex": _probe_openalex,
    "osv": _probe_osv,
    "deps": _probe_deps,
    "gnews": _probe_gnews,
    "marginalia": _probe_marginalia,
    "mwmbl": _probe_mwmbl,
    "archive_org": _probe_archive_org,
    "youtube": _probe_youtube,
    "github_code": _probe_github_code,
    "github_repos": _probe_github_repos,
    "hackernews": _probe_hackernews,
    "huggingface": _probe_huggingface,
    "reddit": _probe_reddit,
    "fyin": _probe_fyin,
    "cheapshark": _probe_cheapshark,
    "slickdeals": _probe_slickdeals,
    "searchcode_repo": _probe_searchcode_repo,
}

# Gate semantic_scholar probe on API key presence.
# When no key is set the engine is disabled; probing it would return DOWN
# (no-key 429) which is misleading.  Register the probe only when a key exists.
if os.environ.get("MONSTER_SEMANTIC_SCHOLAR_API_KEY", ""):
    _PROBES["semantic_scholar"] = _probe_semantic_scholar

# Gate grepapp probe on MONSTER_GREPAPP_ENABLED.
# grep.app rate-limits whole address ranges outright — probing it when
# disabled would always show DOWN, which is misleading.
if os.environ.get("MONSTER_GREPAPP_ENABLED", "").lower() in ("1", "true", "yes"):
    _PROBES["grepapp"] = _probe_grepapp


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_health(
    config: Config | None = None,
    *,
    max_workers: int = 12,
) -> dict[str, bool]:
    """Check all search engines by issuing real probe queries in parallel.

    Returns a dict mapping engine name → bool (True = UP, False = DOWN).

    The parallel probe design keeps total wall-clock time under 60s even
    though several probes have 10-15s timeouts individually.

    For richer diagnostics (failure reasons), call check_health_detailed()
    which returns (status_dict, reasons_dict).
    """
    status, _ = check_health_detailed(config, max_workers=max_workers)
    return status


def check_health_detailed(
    config: Config | None = None,
    *,
    max_workers: int = 12,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Check all engines in parallel.  Returns (status, reasons).

    status:  engine → bool
    reasons: engine → failure reason string (empty string if UP)
    """
    full = check_health_with_latency(config, max_workers=max_workers)
    status = {name: rec["up"] for name, rec in full.items()}
    reasons = {name: rec["reason"] for name, rec in full.items()}
    return status, reasons


def check_health_with_latency(
    config: Config | None = None,
    *,
    max_workers: int = 12,
) -> dict[str, dict]:
    """Check all engines in parallel, capturing per-probe wall-clock latency.

    Returns engine → {"up": bool, "latency_ms": int, "reason": str}.
    Latency is measured around the probe call (perf_counter delta) — it
    includes network RTT, TLS handshake, and any subprocess overhead.
    """
    config = config or Config()
    out: dict[str, dict] = {}

    def _timed(probe: Callable[[Config], tuple[bool, str]]) -> tuple[bool, str, int]:
        t0 = time.perf_counter()
        try:
            is_up, reason = probe(config)
        except Exception as exc:  # noqa: BLE001
            is_up = False
            reason = f"probe raised {type(exc).__name__}: {exc}"
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return is_up, reason, latency_ms

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_timed, probe): name
            for name, probe in _PROBES.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                is_up, reason, latency_ms = future.result()
            except Exception as exc:  # noqa: BLE001
                is_up = False
                reason = f"future raised {type(exc).__name__}: {exc}"
                latency_ms = 0
            out[name] = {"up": is_up, "latency_ms": latency_ms, "reason": reason}

    return out
