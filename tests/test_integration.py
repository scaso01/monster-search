"""Integration tests -- these talk to real services.

They are the counterpart to the unit suite, which mocks every HTTP call. Run
them when you want to know whether the engines actually work against the
services you have running, rather than whether the parsing code is correct.

    pytest tests/test_integration.py -m integration          # all of them
    pytest tests/ -m "not integration"                       # skip them (CI does this)

Every test skips itself when the service it needs is unreachable or
unconfigured, so a partial deployment gives you skips rather than failures.
The self-hosted engines read their URLs from the environment and default to
localhost, so point MONSTER_*_URL at your host before running these.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest
from dotenv import load_dotenv

from monster_search.config import Config

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _load_real_env():
    """Load .env for the live runs, without overriding the shell.

    override=False so an explicit `MONSTER_X=... pytest` beats the .env file,
    which is the precedence people expect and the only way to configure a
    one-off live run against a different host.
    """
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=False)


@pytest.fixture
def config() -> Config:
    return Config()


# --- reachability helpers ------------------------------------------------


def _reachable(url: str, timeout: float = 4.0) -> bool:
    """True when something is listening on the host and port of `url`."""
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def _require(url: str, name: str) -> None:
    if not _reachable(url):
        pytest.skip(f"{name} not reachable at {url}")


def _require_internet() -> None:
    if not _reachable("https://example.com"):
        pytest.skip("no outbound internet")


def _assert_usable(results, source: str) -> None:
    """Every engine must return results that are actually renderable."""
    assert results, f"{source} returned no results"
    for r in results:
        assert r.url, f"{source} returned a result with no URL"
        assert r.title, f"{source} returned a result with no title"


def _require_searxng_upstreams(config: Config, query: str = "python") -> None:
    """Skip when SearXNG itself has no working providers left.

    SearXNG aggregates other engines, and those engines rate-limit, serve
    CAPTCHAs and suspend instances on their own schedule. When every provider
    is in that state SearXNG answers 200 with zero results and names them in
    `unresponsive_engines`. That is an outage upstream of this project, so the
    tests that depend on it skip with the reason rather than failing and
    pointing the blame here. A SearXNG that returns results while our client
    parses none of them still fails, which is the case worth catching.
    """
    import httpx as _httpx

    try:
        data = _httpx.get(
            f"{config.searxng_url}/search",
            params={"q": query, "format": "json"},
            timeout=25,
        ).json()
    except _httpx.HTTPError as exc:
        pytest.skip(f"SearXNG unreachable: {exc}")

    if not data.get("results"):
        pytest.skip(
            "SearXNG has no working providers right now: "
            f"{data.get('unresponsive_engines')}"
        )


def _assert_searxng_usable(results, config: Config, query: str, source: str) -> None:
    """Fail only when SearXNG had providers and our client still saw nothing.

    Probing upstreams before the search is not enough on its own: the providers
    rate-limit and suspend between two requests a second apart, so the guard
    could pass and the real query still come back empty through no fault of
    ours. Re-checking with the query that actually failed is what separates
    "their engines are down" from "we cannot parse a good response".
    """
    if results:
        _assert_usable(results, source)
        return
    _require_searxng_upstreams(config, query)
    pytest.fail(f"{source} returned nothing while SearXNG had working providers")


# --- self-hosted: SearXNG ------------------------------------------------


def test_searxng_search(config):
    from monster_search.clients.searxng import SearXNGClient

    _require(config.searxng_url, "searxng")
    _require_searxng_upstreams(config)
    results = SearXNGClient(config=config).search("python asyncio", max_results=3)
    _assert_searxng_usable(results, config, "python asyncio", "searxng")


def test_searxng_news_category(config):
    from monster_search.clients.searxng import SearXNGClient

    _require(config.searxng_url, "searxng")
    _require_searxng_upstreams(config)
    results = SearXNGClient(config=config).search(
        "technology", category="news", max_results=3
    )
    _assert_searxng_usable(results, config, "technology", "searxng news")


def test_searxng_shopping_category(config):
    """The engine that `--engine shopping` used to skip over entirely.

    Results depend on which shopping engines the SearXNG instance enables, and
    many instances ship that category with none working, so an empty list is a
    valid answer here. What is asserted is that the call succeeds and that any
    results it does return are renderable.
    """
    import httpx as _httpx

    from monster_search.clients.shopping import ShoppingSearchClient

    _require(config.searxng_url, "searxng")
    results = ShoppingSearchClient(config=config).search("laptop", max_results=3)

    if not results:
        probe = _httpx.get(
            f"{config.searxng_url}/search",
            params={"q": "laptop", "format": "json", "categories": "shopping"},
            timeout=20,
        ).json()
        pytest.skip(
            "this SearXNG instance has no working shopping engines: "
            f"{probe.get('unresponsive_engines')}"
        )

    _assert_usable(results, "searxng_shopping")


def test_news_engine(config):
    from monster_search.clients.news import NewsSearchClient

    _require(config.searxng_url, "searxng")
    _require_searxng_upstreams(config)
    _, results = NewsSearchClient(config=config).search("technology", max_results=3)
    _assert_searxng_usable(results, config, "technology", "news")


# --- self-hosted: everything else ----------------------------------------


def test_crawl4ai_extracts_a_page(config):
    from monster_search.clients.crawl4ai_client import Crawl4AIClient

    _require(config.crawl4ai_url, "crawl4ai")
    content, _ = Crawl4AIClient(config=config).search("https://example.com")
    assert "example" in content.lower()


def test_zoekt_code_search(config):
    from monster_search.clients.zoekt import ZoektClient

    _require(config.zoekt_url, "zoekt")
    results = ZoektClient(config=config).search("def ", max_results=3)
    _assert_usable(results, "zoekt")


def test_whodat_lookup(config):
    from monster_search.clients.whodat import WhoDatClient

    _require(config.whodat_url, "whodat")
    results = WhoDatClient(config=config).search("example.com")
    _assert_usable(results, "whodat")


def test_meilisearch_cache_is_queryable(config):
    """An empty cache is fine; the call must not raise."""
    from monster_search.clients.meilisearch_client import MeilisearchClient

    _require(config.meilisearch_url, "meilisearch")
    results = MeilisearchClient(config=config).search("python", max_results=3)
    assert isinstance(results, list)


def test_changedetection_lists_watches(config):
    from monster_search.clients.changedetection_client import ChangeDetectionClient

    _require(config.changedetection_url, "changedetection")
    if not config.changedetection_api_key:
        pytest.skip("MONSTER_CHANGEDETECTION_API_KEY not set")
    assert isinstance(ChangeDetectionClient(config=config).list_watches(), list)


def test_local_researcher_is_up(config):
    """The engine itself takes 3-8 minutes, so only its health is asserted."""
    from monster_search.health import check_health

    _require(config.local_researcher_url, "local_researcher")
    assert check_health(config).get("local_researcher") is True


# --- external APIs, no credentials ---------------------------------------


def test_arxiv(config):
    from monster_search.clients.arxiv import ArxivClient

    _require_internet()
    _assert_usable(ArxivClient(config=config).search("transformer", max_results=3), "arxiv")


def test_openalex(config):
    """OpenAlex meters requests against a daily budget per source address.

    Once that budget is spent it answers 429 with "Insufficient budget ...
    Resets at midnight UTC" for everything, including requests that carry a
    mailto for the polite pool. Behind a shared egress address the budget can
    be gone before this suite makes its first call, which is throttling rather
    than a broken client, so it skips — same treatment as reddit above. A
    response that arrives but is not usable still fails.
    """
    import httpx as _httpx

    from monster_search.clients.openalex import OpenAlexClient

    _require_internet()
    try:
        results = OpenAlexClient(config=config).search("machine learning", max_results=3)
    except _httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            pytest.skip("openalex daily budget exhausted (429), resets at midnight UTC")
        raise

    _assert_usable(results, "openalex")


def test_osv_vulnerabilities(config):
    from monster_search.clients.osv import OsvClient

    _require_internet()
    _assert_usable(OsvClient(config=config).search("pypi:jinja2", max_results=3), "osv")


def test_deps_package_metadata(config):
    from monster_search.clients.deps import DepsClient

    _require_internet()
    _assert_usable(DepsClient(config=config).search("npm:express"), "deps")


def test_gnews(config):
    from monster_search.clients.gnews import GNewsClient

    _require_internet()
    _assert_usable(GNewsClient(config=config).search("cybersecurity", max_results=3), "gnews")


def test_hackernews(config):
    from monster_search.clients.hackernews import HackerNewsClient

    _require_internet()
    _assert_usable(
        HackerNewsClient(config=config).search("rust", max_results=3), "hackernews"
    )


def test_huggingface(config):
    from monster_search.clients.huggingface import HuggingFaceClient

    _require_internet()
    _assert_usable(
        HuggingFaceClient(config=config).search("text generation", max_results=3),
        "huggingface",
    )


def test_reddit(config):
    """Regression guard: this engine was returning nothing at all until 0.10.1.

    Reddit rate-limits the feed per source address after a handful of requests,
    so running this repeatedly in one sitting provokes a 429. That is throttling
    rather than a broken engine, and it skips. A response that arrives but is
    not a feed still fails, which is the failure mode that actually mattered.
    """
    import httpx as _httpx

    from monster_search.clients.reddit import RedditClient

    _require_internet()
    try:
        results = RedditClient(config=config).search("self hosted search", max_results=3)
    except _httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            pytest.skip("rate limited by reddit (429), try again in a few minutes")
        raise

    _assert_usable(results, "reddit")


def test_marginalia(config):
    from monster_search.clients.marginalia import MarginaliaClient

    _require_internet()
    _assert_usable(
        MarginaliaClient(config=config).search("independent search", max_results=3),
        "marginalia",
    )


def test_mwmbl(config):
    from monster_search.clients.mwmbl import MwmblClient

    _require_internet()
    _assert_usable(MwmblClient(config=config).search("python", max_results=3), "mwmbl")


def test_archive_org(config):
    from monster_search.clients.archive_org import ArchiveOrgClient

    _require_internet()
    _assert_usable(
        ArchiveOrgClient(config=config).search("python programming", max_results=3),
        "archive_org",
    )


def test_cheapshark_game_deals(config):
    from monster_search.clients.cheapshark import CheapSharkClient

    _require_internet()
    _assert_usable(
        CheapSharkClient(config=config).search("Portal", max_results=3), "cheapshark"
    )


def test_slickdeals(config):
    from monster_search.clients.slickdeals import SlickdealsClient

    _require_internet()
    _assert_usable(SlickdealsClient(config=config).search("ssd", max_results=3), "slickdeals")


def test_deals_rss(config):
    from monster_search.clients.deals_rss import DealsRSSClient

    _require_internet()
    results = asyncio.run(DealsRSSClient(config=config).asearch("ssd", max_results=3))
    assert isinstance(results, list)


def test_youtube(config):
    from monster_search.clients.youtube import YouTubeClient

    _require_internet()
    _assert_usable(
        YouTubeClient(config=config).search("rust async", max_results=2), "youtube"
    )


def test_semantic_scholar(config):
    from monster_search.clients.semantic_scholar import SemanticScholarClient

    _require_internet()
    if not config.semantic_scholar_api_key:
        pytest.skip("MONSTER_SEMANTIC_SCHOLAR_API_KEY not set")
    _assert_usable(
        SemanticScholarClient(config=config).search("attention", max_results=3),
        "semantic_scholar",
    )


# --- engines that shell out ----------------------------------------------


def test_github_repos(config):
    from monster_search.clients.github_repos import GithubReposClient

    if not shutil.which("gh"):
        pytest.skip("gh CLI not installed")
    _require_internet()
    _assert_usable(
        GithubReposClient(config=config).search("search engine", max_results=3),
        "github_repos",
    )


def test_github_code(config):
    from monster_search.clients.github_code import GithubCodeClient

    if not shutil.which("gh"):
        pytest.skip("gh CLI not installed")
    _require_internet()
    _assert_usable(
        GithubCodeClient(config=config).search("func main", max_results=3), "github_code"
    )


def test_fyin_over_ssh(config):
    """Requires MONSTER_SSH_HOST and fyin installed on that host."""
    from monster_search.clients.fyin import FyinClient

    if not config.ssh_host:
        pytest.skip("MONSTER_SSH_HOST not set")
    message, _ = FyinClient(config=config).search("rust async")
    assert message


def test_searchcode_repo(config):
    """Opt-in engine: needs an explicit repository, so it is never in a sweep."""
    from monster_search.clients.searchcode_repo import SearchcodeRepoClient

    _require_internet()
    results = SearchcodeRepoClient(config=config).search(
        "import", repository="https://github.com/encode/httpx", max_results=3
    )
    _assert_usable(results, "searchcode_repo")


# --- scrapers ------------------------------------------------------------
#
# These parse retail HTML, which changes without notice and is served
# differently to different addresses. A block or a layout change is not a
# defect in this project, so an empty list is tolerated; a result that comes
# back malformed is not.


def test_amazon_deals(config):
    from monster_search.clients.amazon_deals import AmazonDealsClient

    _require_internet()
    results = AmazonDealsClient(config=config).search("ssd", max_results=3)
    if not results:
        pytest.skip("Amazon returned nothing, most likely a block or a layout change")
    _assert_usable(results, "amazon_deals")


def test_newegg(config):
    from monster_search.clients.newegg import NeweggClient

    _require_internet()
    results = NeweggClient(config=config).search("ssd", max_results=3)
    if not results:
        pytest.skip("Newegg returned nothing, most likely a block or a layout change")
    _assert_usable(results, "newegg")


def test_priceghost(config):
    """Self-hosted price tracker. Needs credentials as well as a reachable host."""
    from monster_search.clients.priceghost import PriceGhostClient

    _require(config.priceghost_url, "priceghost")
    if not (config.priceghost_email and config.priceghost_password):
        pytest.skip("MONSTER_PRICEGHOST_EMAIL / _PASSWORD not set")
    assert isinstance(
        PriceGhostClient(config=config).search("ssd", max_results=3), list
    )


def test_perplexity(config):
    """Needs a logged-in perplexity.ai session, from a browser profile or a cookie.

    An expired cookie is a configuration problem on this machine rather than a
    defect here, and the client now says so explicitly instead of handing back
    an empty answer, so that case skips.
    """
    from monster_search.clients.perplexity_client import PerplexityClient

    _require_internet()
    if not (config.perplexity_cookies_from_browser or config.perplexity_session_token):
        pytest.skip("no Perplexity session configured")

    try:
        message, _ = PerplexityClient(config=config).search("what is rust async")
    except (ValueError, RuntimeError) as exc:
        pytest.skip(f"Perplexity session unusable: {exc}")

    assert message


def test_grepapp(config):
    """Off by default because grep.app rate-limits whole address ranges."""
    from monster_search.clients.grepapp import GrepAppClient

    _require_internet()
    if not config.grepapp_enabled:
        pytest.skip("MONSTER_GREPAPP_ENABLED not set")
    try:
        results = GrepAppClient(config=config).search("async fn main", max_results=3)
    except RuntimeError as exc:
        # grep.app blocks whole hosting and VPN ranges, which is the reason the
        # engine ships disabled. Reporting that as our failure would be wrong.
        if "429" in str(exc):
            pytest.skip(f"grep.app rate-limited this address: {exc}")
        raise
    _assert_usable(results, "grepapp")


# --- watch subcommands, end to end ---------------------------------------


def test_watch_round_trip(config):
    """Add a watch, find it, read it, then remove it again.

    The removal is the point: this creates real state on a real service, so it
    cleans up after itself even when an assertion in the middle fails.
    """
    from monster_search.clients.changedetection_client import ChangeDetectionClient

    _require(config.changedetection_url, "changedetection")
    if not config.changedetection_api_key:
        pytest.skip("MONSTER_CHANGEDETECTION_API_KEY not set")

    client = ChangeDetectionClient(config=config)
    created = client.add_watch("https://example.com", tag="monster-search-selftest")
    uuid = created.get("uuid") if isinstance(created, dict) else None
    assert uuid, f"add_watch returned no uuid: {created!r}"

    try:
        listed = client.list_watches()
        assert any(w.get("uuid") == uuid for w in listed), "new watch missing from list"
        # A brand new watch has not been fetched yet, so the snapshot may be
        # empty. It must still be a string rather than an error.
        assert isinstance(client.get_latest(uuid), str)
    finally:
        assert client.remove_watch(uuid) is True


# --- AI engines (slow: roughly two minutes each) -------------------------


@pytest.mark.slow
def test_vane_ai_search(config):
    from monster_search.clients.vane import VaneClient

    _require(config.vane_url, "vane")
    message, results = VaneClient(config=config).search("what is rust async")
    assert message
    assert isinstance(results, list)


@pytest.mark.slow
def test_khoj_ai_search(config):
    from monster_search.clients.khoj import KhojClient

    _require(config.khoj_url, "khoj")
    message, results = KhojClient(config=config).search("what is rust async")
    assert message
    assert isinstance(results, list)


@pytest.mark.slow
def test_synthesizer(config):
    """Needs SearXNG for sources and an OpenAI-compatible LLM to write them up.

    The synthesizer returns an empty answer by design when SearXNG gives it
    nothing to work from, so that a smart search falls back to the other
    engines rather than failing. A SearXNG instance whose upstream providers
    are all in cooldown therefore produces a legitimate empty result, not a
    failure, and this skips rather than asserting on it.
    """
    from monster_search.clients.searxng import SearXNGClient
    from monster_search.clients.synthesizer import SynthesizerClient

    _require(config.searxng_url, "searxng")
    _require(config.llama_url, "llama-server")

    _require_searxng_upstreams(config)
    if not SearXNGClient(config=config).search("what is rust async", max_results=3):
        pytest.skip("SearXNG returned no sources, nothing for the synthesizer to write")

    message, results = SynthesizerClient(config=config).search("what is rust async")
    assert message
    assert isinstance(results, list)


# --- end to end ----------------------------------------------------------


@pytest.mark.slow
def test_smart_search_end_to_end(config):
    """The default CLI path: many engines in parallel, fused into one list."""
    from monster_search.clients.all_engines import AllEnginesClient

    _require(config.searxng_url, "searxng")
    message, _answer, results = asyncio.run(
        AllEnginesClient(config=config).smart_search("python asyncio", max_results=10)
    )
    assert message
    _assert_usable(results, "smart_search")
    assert len({r.url for r in results}) == len(results), "fused results contain duplicates"


def test_health_reports_at_least_one_service_up(config):
    from monster_search.health import check_health

    _require(config.searxng_url, "searxng")
    status = check_health(config)
    assert any(status.values()), "no service reported UP"
