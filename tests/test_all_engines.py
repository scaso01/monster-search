from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monster_search.clients.all_engines import AllEnginesClient
from monster_search.models import SearchResult


# Always-on engines (present for every query)
ALWAYS_ON = frozenset({
    "searxng", "marginalia", "mwmbl", "news", "gnews",
    "perplexity", "synthesizer", "youtube",
    "hackernews", "reddit", "github_repos",
})

# Router-gated specialist engines
ACADEMIC_ENGINES = frozenset({"arxiv", "semantic_scholar", "openalex"})
SECURITY_ENGINES = frozenset({"osv"})
PACKAGE_ENGINES = frozenset({"deps"})
WHOIS_ENGINES = frozenset({"whodat"})
CODE_ENGINES = frozenset({"zoekt", "grepapp", "github_code"})
AI_ML_ENGINES = frozenset({"huggingface"})
ARCHIVE_ENGINES = frozenset({"archive_org"})


def _result(url: str, title: str, source: str = "test") -> SearchResult:
    return SearchResult(url=url, title=title, snippet="", source=source, published="")


def _patch_engines(**overrides):
    """Return a dict of patches for all engine classes.

    Each engine class is patched so its constructor returns a mock with
    the specified async return value.  Pass engine name -> return value
    to override defaults.

    All 18 client classes are patched (including router-gated ones that
    may or may not appear in _build_engines), so mocks are ready
    regardless of which engines the router selects.
    """
    defaults = {
        "SearXNGClient": [_result("https://searxng.com/1", "SearXNG Result", "searxng")],
        "MarginaliaClient": [_result("https://marginalia.com/1", "Marginalia Result", "marginalia")],
        "MwmblClient": [_result("https://mwmbl.com/1", "Mwmbl Result", "mwmbl")],
        "NewsSearchClient": ("", [_result("https://news.com/1", "News Result", "searxng")]),
        "PerplexityClient": ("AI answer", [_result("https://perplexity.com/1", "Perplexity Result", "perplexity")]),
        "SynthesizerClient": ("Synthesized answer", [_result("https://synth.com/1", "Synth Result", "synthesizer")]),
        "ArchiveOrgClient": [_result("https://archive.org/1", "Archive Result", "archive_org")],
        "ArxivClient": [_result("https://arxiv.org/1", "arXiv Result", "arxiv")],
        "SemanticScholarClient": [_result("https://semanticscholar.org/1", "SS Result", "semantic_scholar")],
        "OpenAlexClient": [_result("https://openalex.org/1", "OpenAlex Result", "openalex")],
        "OsvClient": [_result("https://osv.dev/1", "OSV Result", "osv")],
        "DepsClient": [_result("https://deps.dev/1", "Deps Result", "deps")],
        "GNewsClient": [_result("https://gnews.com/1", "GNews Result", "gnews")],
        "WhoDatClient": [_result("https://whodat.com/1", "WhoDat Result", "whodat")],
        "ZoektClient": [_result("https://zoekt.com/1", "Zoekt Result", "zoekt")],
        "VaneClient": ("Vane answer", [_result("https://vane.com/1", "Vane Result", "vane")]),
        "KhojClient": ("Khoj answer", [_result("https://khoj.com/1", "Khoj Result", "khoj")]),
        "FyinClient": ("Fyin answer", [_result("https://fyin.com/1", "Fyin Result", "fyin")]),
        "YouTubeClient": [_result("https://youtube.com/1", "YouTube Result", "youtube")],
        "GrepAppClient": [_result("https://grep.app/1", "GrepApp Result", "grepapp")],
        "GithubCodeClient": [_result("https://github.com/code/1", "GithubCode Result", "github_code")],
        "HackerNewsClient": [_result("https://hn.com/1", "HN Result", "hackernews")],
        "HuggingFaceClient": [_result("https://huggingface.co/1", "HF Result", "huggingface")],
        "RedditClient": [_result("https://reddit.com/1", "Reddit Result", "reddit")],
        "GithubReposClient": [_result("https://github.com/repos/1", "GH Repos Result", "github_repos")],
    }
    defaults.update(overrides)

    patches = {}
    base = "monster_search.clients.all_engines"
    for cls_name, return_val in defaults.items():
        mock_cls = patch(f"{base}.{cls_name}")
        patches[cls_name] = (mock_cls, return_val)
    return patches


class _EngineContext:
    """Context manager that patches all engine classes + breaker + cache."""

    def __init__(self, **overrides):
        self._patches = _patch_engines(**overrides)
        self._mocks = {}
        # Patch circuit breaker to be a passthrough
        self._breaker_patch = patch(
            "monster_search.clients.all_engines.get_breaker",
        )
        # Patch cache to be a no-op
        self._cache_patch = patch.object(
            AllEnginesClient, "_cache_results", new_callable=AsyncMock,
        )

    def __enter__(self):
        # Breaker passthrough: get_breaker(name).call(coro) awaits and returns the coro result
        mock_breaker_fn = self._breaker_patch.__enter__()

        async def _passthrough_call(coro):
            return await coro

        mock_breaker = AsyncMock()
        mock_breaker.call = _passthrough_call
        mock_breaker_fn.return_value = mock_breaker

        self._cache_patch.__enter__()

        for cls_name, (patcher, return_val) in self._patches.items():
            mock_cls = patcher.__enter__()
            if isinstance(return_val, BaseException):
                mock_cls.return_value.asearch = AsyncMock(side_effect=return_val)
                mock_cls.return_value.search = AsyncMock(side_effect=return_val)
            else:
                mock_cls.return_value.asearch = AsyncMock(return_value=return_val)
                mock_cls.return_value.search = AsyncMock(return_value=return_val)
            self._mocks[cls_name] = mock_cls
        return self

    def __exit__(self, *args):
        for _, (patcher, _) in self._patches.items():
            patcher.__exit__(*args)
        self._cache_patch.__exit__(*args)
        self._breaker_patch.__exit__(*args)


# ---------------------------------------------------------------------------
# Core behaviour tests (updated for router-gated engine selection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_general_query_only_always_on():
    """A plain general query should only activate always-on engines."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("mini PC 96GB DDR5")
    # 6 always-on engines, each returns 1 result
    assert len(results) == len(ALWAYS_ON)
    for name in ALWAYS_ON:
        assert name in message
    # No specialist engines present
    for name in ("arxiv", "semantic_scholar", "openalex", "osv",
                 "deps", "whodat", "zoekt", "archive_org"):
        assert name not in message


@pytest.mark.asyncio
async def test_academic_query_adds_academic_engines():
    """An academic query should activate always-on + academic engines.

    semantic_scholar is gated on MONSTER_SEMANTIC_SCHOLAR_API_KEY.  In the
    test environment (no key set) only arxiv + openalex are added; SS must
    be absent.  See test_academic_query_ss_included_when_key_set for the
    key-present path.
    """
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("transformer attention paper")
    # academic engines without semantic_scholar (no key in test env)
    academic_no_ss = frozenset({"arxiv", "openalex"})
    expected = ALWAYS_ON | academic_no_ss
    assert len(results) == len(expected)
    for name in expected:
        assert name in message
    # semantic_scholar must be absent when no key is set
    assert "semantic_scholar" not in message
    # Non-academic specialists should be absent
    for name in ("osv", "deps", "whodat", "zoekt"):
        assert name not in message


@pytest.mark.asyncio
async def test_academic_query_ss_included_when_key_set():
    """When MONSTER_SEMANTIC_SCHOLAR_API_KEY is set, SS joins academic results."""
    import os
    with patch.dict(os.environ, {"MONSTER_SEMANTIC_SCHOLAR_API_KEY": "fake_test_key"}):
        from monster_search.config import Config
        cfg = Config()  # picks up the patched env var
    with _EngineContext():
        client = AllEnginesClient(config=cfg)
        message, results = await client.search("transformer attention paper")
    # semantic_scholar must now be present
    assert "semantic_scholar" in message


@pytest.mark.asyncio
async def test_security_query_adds_osv():
    """A security query should activate always-on + osv."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("CVE-2024-1234 vulnerability")
    expected = ALWAYS_ON | SECURITY_ENGINES
    assert len(results) == len(expected)
    assert "osv" in message


@pytest.mark.asyncio
async def test_package_query_adds_deps():
    """A package query should activate always-on + deps."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("npm:express")
    expected = ALWAYS_ON | PACKAGE_ENGINES
    assert len(results) == len(expected)
    assert "deps" in message


@pytest.mark.asyncio
async def test_whois_query_adds_whodat():
    """A domain query should activate always-on + whodat."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("google.com")
    expected = ALWAYS_ON | WHOIS_ENGINES
    assert len(results) == len(expected)
    assert "whodat" in message


@pytest.mark.asyncio
async def test_code_query_adds_code_engines():
    """A code query should activate always-on + code engines (grepapp absent by default).

    grepapp is gated on MONSTER_GREPAPP_ENABLED.  In the test environment (no
    var set) only zoekt + github_code are added; grepapp must be absent.
    See test_code_query_grepapp_included_when_enabled for the enabled path.
    """
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("def parse_response")
    code_no_grep = frozenset({"zoekt", "github_code"})
    expected = ALWAYS_ON | code_no_grep
    assert len(results) == len(expected)
    assert "zoekt" in message
    assert "github_code" in message
    # grepapp must be absent when MONSTER_GREPAPP_ENABLED is unset
    assert "grepapp" not in message


@pytest.mark.asyncio
async def test_code_query_grepapp_included_when_enabled():
    """When MONSTER_GREPAPP_ENABLED=true, grepapp joins code query results."""
    import os
    with patch.dict(os.environ, {"MONSTER_GREPAPP_ENABLED": "true"}):
        from monster_search.config import Config
        cfg = Config()
    with _EngineContext():
        client = AllEnginesClient(config=cfg)
        message, results = await client.search("def parse_response")
    assert "grepapp" in message


@pytest.mark.asyncio
async def test_archive_query_adds_archive_org():
    """An archive query should activate always-on + archive_org."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("wayback machine old site")
    expected = ALWAYS_ON | ARCHIVE_ENGINES
    assert len(results) == len(expected)
    assert "archive_org" in message


@pytest.mark.asyncio
async def test_url_query_adds_archive_org():
    """A URL query should include archive_org regardless of category."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("https://example.com/page")
    # URL triggers WHOIS (example.com matches) + archive_org (URL override)
    assert "archive_org" in message


@pytest.mark.asyncio
async def test_http_url_adds_archive_org():
    """http:// URL should include archive_org."""
    with _EngineContext():
        client = AllEnginesClient()
        message, results = await client.search("http://old-site.org/docs")
    assert "archive_org" in message


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graceful_degradation():
    """Failed engines are reported in the message but don't crash."""
    with _EngineContext(
        MarginaliaClient=RuntimeError("connection refused"),
        PerplexityClient=RuntimeError("timeout"),
    ):
        client = AllEnginesClient()
        message, results = await client.search("test query")
    # 6 always-on minus 2 failed = 4
    assert len(results) == len(ALWAYS_ON) - 2
    assert "Failed: marginalia, perplexity" in message
    assert "Succeeded:" in message


@pytest.mark.asyncio
async def test_all_always_on_fail():
    """All always-on engines failing produces empty results without crash."""
    with _EngineContext(
        SearXNGClient=RuntimeError("down"),
        MarginaliaClient=RuntimeError("down"),
        MwmblClient=RuntimeError("down"),
        NewsSearchClient=RuntimeError("down"),
        PerplexityClient=RuntimeError("down"),
        SynthesizerClient=RuntimeError("down"),
        GNewsClient=RuntimeError("down"),
        YouTubeClient=RuntimeError("down"),
        HackerNewsClient=RuntimeError("down"),
        RedditClient=RuntimeError("down"),
        GithubReposClient=RuntimeError("down"),
    ):
        client = AllEnginesClient()
        message, results = await client.search("test query")
    assert results == []
    assert "Failed:" in message


# ---------------------------------------------------------------------------
# Dedup and fusion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_url_deduplication():
    """Duplicate URLs across engines are fused into one result."""
    dup_url = "https://shared.com/page"
    with _EngineContext(
        SearXNGClient=[_result(dup_url, "SearXNG Version", "searxng")],
        MarginaliaClient=[_result(dup_url, "Marginalia Version", "marginalia")],
    ):
        client = AllEnginesClient()
        _, results = await client.search("test")
    urls = [r.url for r in results]
    assert urls.count(dup_url) == 1
    dup_result = [r for r in results if r.url == dup_url][0]
    assert dup_result.title == "Marginalia Version"  # Longer title
    assert dup_result.source == "fused"
    assert set(dup_result.sources) == {"searxng", "marginalia"}
    assert dup_result.fused_score is not None


@pytest.mark.asyncio
async def test_no_fuse_legacy():
    """With fuse=False, first-occurrence-wins dedup is used."""
    dup_url = "https://shared.com/page"
    with _EngineContext(
        SearXNGClient=[_result(dup_url, "SearXNG Version", "searxng")],
        MarginaliaClient=[_result(dup_url, "Marginalia Version", "marginalia")],
    ):
        client = AllEnginesClient()
        _, results = await client.search("test", fuse=False)
    urls = [r.url for r in results]
    assert urls.count(dup_url) == 1
    dup_result = [r for r in results if r.url == dup_url][0]
    assert dup_result.sources is None  # No fusion metadata


# ---------------------------------------------------------------------------
# Message content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_combined_message():
    """AI engine synthesis messages appear in combined output."""
    with _EngineContext(
        SynthesizerClient=("Synthesizer synthesis here", [_result("https://s.com", "S", "synthesizer")]),
    ):
        client = AllEnginesClient()
        message, _ = await client.search("test")
    assert "Synthesizer synthesis here" in message


@pytest.mark.asyncio
async def test_empty_results():
    """All engines returning empty still produces a valid response."""
    with _EngineContext(
        SearXNGClient=[],
        MarginaliaClient=[],
        MwmblClient=[],
        NewsSearchClient=("", []),
        PerplexityClient=("", []),
        SynthesizerClient=("", []),
        GNewsClient=[],
        YouTubeClient=[],
        HackerNewsClient=[],
        RedditClient=[],
        GithubReposClient=[],
    ):
        client = AllEnginesClient()
        message, results = await client.search("test")
    assert results == []
    assert "Succeeded:" in message


# ---------------------------------------------------------------------------
# Engine exclusion validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perplexica_removed():
    """Perplexica (replaced by Synthesizer) should never appear in results."""
    with _EngineContext():
        client = AllEnginesClient()
        # Use an academic query to get the broadest engine set we can
        message, _ = await client.search("transformer attention paper")
    assert "perplexica" not in message


@pytest.mark.asyncio
async def test_meilisearch_removed_from_results():
    """Meilisearch should not appear in search results (background cache only)."""
    with _EngineContext():
        client = AllEnginesClient()
        message, _ = await client.search("test query")
    assert "meilisearch" not in message


# ---------------------------------------------------------------------------
# smart_search — academic engine gate (Fix B)
# ---------------------------------------------------------------------------


def _patch_smart_search_engines(**overrides):
    """Patch all engine classes used by smart_search + tiered_search infrastructure.

    tiered_search is patched to immediately return empty (results, answers,
    status) so tests can focus on which engines were added to the engines dict,
    not tier logic.
    """
    patches = _patch_engines(**overrides)
    # Add shopping clients that appear in _make_engines but not _build_engines defaults
    shopping_clients = [
        "ShoppingSearchClient", "SlickdealsClient",
        "CheapSharkClient", "DealsRSSClient",
        "PriceGhostClient", "AmazonDealsClient", "NeweggClient",
        "MeilisearchClient", "LocalResearcherClient",
    ]
    base = "monster_search.clients.all_engines"
    for cls_name in shopping_clients:
        if cls_name not in patches:
            mock_cls = patch(f"{base}.{cls_name}")
            patches[cls_name] = (mock_cls, [])
    return patches


class _SmartSearchContext:
    """Context manager that patches all engine classes for smart_search tests.

    Captures the ``engines`` dict that smart_search passes to tiered_search,
    so tests can assert which engine keys are present or absent.
    """

    def __init__(self, **overrides):
        self._patches = _patch_smart_search_engines(**overrides)
        self._mocks = {}
        self._breaker_patch = patch("monster_search.clients.all_engines.get_breaker")
        self._cache_patch = patch.object(
            AllEnginesClient, "_cache_results", new_callable=AsyncMock,
        )
        self._tiered_patch = patch("monster_search.clients.all_engines.tiered_search")
        self.captured_engines: dict = {}

    def __enter__(self):
        mock_breaker_fn = self._breaker_patch.__enter__()

        async def _passthrough_call(coro):
            return await coro

        mock_breaker = MagicMock()
        mock_breaker.call = _passthrough_call
        mock_breaker_fn.return_value = mock_breaker

        self._cache_patch.__enter__()

        # Patch tiered_search: capture engines dict, return empty (results, answers, status)
        mock_tiered = self._tiered_patch.__enter__()

        async def _capture_tiered(query, engines, **kwargs):
            self.captured_engines = dict(engines)
            return [], {}, {}

        mock_tiered.side_effect = _capture_tiered

        for cls_name, (patcher, return_val) in self._patches.items():
            mock_cls = patcher.__enter__()
            if isinstance(return_val, BaseException):
                mock_cls.return_value.asearch = AsyncMock(side_effect=return_val)
                mock_cls.return_value.search = AsyncMock(side_effect=return_val)
            else:
                mock_cls.return_value.asearch = AsyncMock(return_value=return_val)
                mock_cls.return_value.search = AsyncMock(return_value=return_val)
            self._mocks[cls_name] = mock_cls
        return self

    def __exit__(self, *args):
        for _, (patcher, _) in self._patches.items():
            patcher.__exit__(*args)
        self._cache_patch.__exit__(*args)
        self._tiered_patch.__exit__(*args)
        self._breaker_patch.__exit__(*args)


@pytest.mark.asyncio
async def test_smart_search_general_excludes_academic():
    """A general query to smart_search must NOT include academic engines."""
    with _SmartSearchContext() as ctx:
        client = AllEnginesClient()
        await client.smart_search("best programming language 2026")
    for engine in ("arxiv", "semantic_scholar", "openalex"):
        assert engine not in ctx.captured_engines, (
            f"Academic engine '{engine}' was included for a general query"
        )


@pytest.mark.asyncio
async def test_smart_search_academic_query_includes_academic():
    """An academic query to smart_search MUST include arxiv + openalex.

    semantic_scholar is gated on MONSTER_SEMANTIC_SCHOLAR_API_KEY.  When no
    key is set (test environment default) SS must be absent.  The key-present
    path is covered by test_smart_search_academic_ss_included_when_key_set.
    """
    with _SmartSearchContext() as ctx:
        client = AllEnginesClient()
        await client.smart_search("attention is all you need transformer paper")
    for engine in ("arxiv", "openalex"):
        assert engine in ctx.captured_engines, (
            f"Academic engine '{engine}' was missing for an academic query"
        )
    assert "semantic_scholar" not in ctx.captured_engines, (
        "semantic_scholar should be absent when no API key is configured"
    )


@pytest.mark.asyncio
async def test_smart_search_academic_ss_included_when_key_set():
    """When MONSTER_SEMANTIC_SCHOLAR_API_KEY is set, SS is included for academic queries."""
    import os
    with patch.dict(os.environ, {"MONSTER_SEMANTIC_SCHOLAR_API_KEY": "fake_test_key"}):
        from monster_search.config import Config
        cfg = Config()
    with _SmartSearchContext() as ctx:
        client = AllEnginesClient(config=cfg)
        await client.smart_search("attention is all you need transformer paper")
    assert "semantic_scholar" in ctx.captured_engines, (
        "semantic_scholar should be present when MONSTER_SEMANTIC_SCHOLAR_API_KEY is set"
    )


_SHOPPING_ENGINES = (
    "searxng_shopping", "slickdeals", "cheapshark", "deals_rss",
    "priceghost", "amazon_deals", "newegg",
)


@pytest.mark.asyncio
async def test_smart_search_general_excludes_shopping():
    """A general/technical query must NOT include shopping engines — they return
    generic current deals regardless of relevance (gating mirrors _build_engines)."""
    with _SmartSearchContext() as ctx:
        client = AllEnginesClient()
        await client.smart_search("agentic AI orchestration with MCP servers")
    for engine in _SHOPPING_ENGINES:
        assert engine not in ctx.captured_engines, (
            f"Shopping engine '{engine}' was included for a non-shopping query"
        )


@pytest.mark.asyncio
async def test_smart_search_shopping_query_includes_shopping():
    """An explicit shopping query MUST include the shopping/deal engines."""
    with _SmartSearchContext() as ctx:
        client = AllEnginesClient()
        await client.smart_search("buy cheapest RTX 4090 under $1500")
    for engine in _SHOPPING_ENGINES:
        assert engine in ctx.captured_engines, (
            f"Shopping engine '{engine}' was missing for a shopping query"
        )


# ---------------------------------------------------------------------------
# smart_search — specialist engine gates (security/package/whois/code/ai_ml/archive)
# now mirror _build_engines instead of running on every query.
# ---------------------------------------------------------------------------

# (category-triggering query, engine that must appear) — queries verified to
# classify into the intended QueryCategory by classify_query.
_SPECIALIST_POSITIVE = [
    ("log4j vulnerability CVE-2021-44228", "osv"),        # SECURITY
    ("pypi:requests", "deps"),                            # PACKAGE
    ("whois example.com", "whodat"),                      # WHOIS
    ("def parse_config(path)", "zoekt"),                  # CODE
    ("def parse_config(path)", "github_code"),            # CODE
    ("llm fine-tuning lora", "huggingface"),              # AI_ML
    ("wayback machine snapshot of a site", "archive_org"),  # ARCHIVE
]

# Specialists that must NOT appear for a plain general/technical query.
_SPECIALIST_ENGINES = ["osv", "deps", "whodat", "zoekt", "github_code",
                       "huggingface", "archive_org"]


@pytest.mark.parametrize("query,engine", _SPECIALIST_POSITIVE)
@pytest.mark.asyncio
async def test_smart_search_category_includes_specialist(query, engine):
    """Each specialist engine is present for a query in its category."""
    with _SmartSearchContext() as ctx:
        await AllEnginesClient().smart_search(query)
    assert engine in ctx.captured_engines, (
        f"'{engine}' missing for category query {query!r}"
    )


@pytest.mark.parametrize("engine", _SPECIALIST_ENGINES)
@pytest.mark.asyncio
async def test_smart_search_general_excludes_specialists(engine):
    """A general query must run NONE of the category-gated specialists."""
    with _SmartSearchContext() as ctx:
        await AllEnginesClient().smart_search("agentic AI orchestration with MCP servers")
    assert engine not in ctx.captured_engines, (
        f"'{engine}' wrongly included for a general query"
    )


# ---------------------------------------------------------------------------
# smart_search_rich — answer capture, best-answer selection, engine_status
# ---------------------------------------------------------------------------


def test_select_best_answer_preference_order():
    """Best answer follows source preference; whitespace/empty are ignored."""
    from monster_search.clients.all_engines import _select_best_answer
    assert _select_best_answer({"synthesizer": "s", "perplexity": "p"}) == "p"
    assert _select_best_answer({"synthesizer": "s", "khoj": "k"}) == "k"
    assert _select_best_answer({"synthesizer": "s"}) == "s"
    assert _select_best_answer({}) == ""
    # an unknown engine is still used as a last resort
    assert _select_best_answer({"mystery": "m"}) == "m"
    # whitespace-only answers don't count
    assert _select_best_answer({"perplexity": "   "}) == ""


def _passthrough_breaker():
    async def _call(coro):
        return await coro
    mb = MagicMock()
    mb.call = _call
    return mb


@pytest.mark.asyncio
async def test_smart_search_rich_selects_best_answer_and_reports_status():
    """smart_search_rich returns the provenance dict, prefers Perplexity's answer
    over the local synthesizer, and reports per-engine status (incl. synthesizer)."""
    import monster_search.clients.all_engines as ae
    r = SearchResult(title="t", url="https://e.com", snippet="s", source="perplexity")

    # synthesizer is captured separately (run outside the tiers), so a real
    # tiered_search never returns it in its answers dict.
    async def _tiered(query, engines, **kwargs):
        return (
            [r],
            {"perplexity": "pplx ans"},
            {"perplexity": {"state": "ok", "count": 1, "results": [r]},
             "searxng": {"state": "empty", "count": 0, "results": []}},
        )

    with patch.object(ae, "get_breaker", return_value=_passthrough_breaker()), \
            patch.object(ae, "tiered_search", side_effect=_tiered), \
            patch.object(ae, "SynthesizerClient") as MockSynth:
        MockSynth.return_value.asearch = AsyncMock(return_value=("", []))
        out = await AllEnginesClient().smart_search_rich("best language 2026")

    assert set(out) >= {"message", "answer", "answers", "results", "engine_status", "per_engine"}
    assert out["answer"] == "pplx ans"           # perplexity beats local synthesizer
    assert out["answers"]["perplexity"] == "pplx ans"
    assert out["results"] == [r]
    assert out["engine_status"]["perplexity"]["state"] == "ok"
    # synthesizer ran separately and is reported even with no answer
    assert out["engine_status"]["synthesizer"]["state"] == "empty"
    # raw per-engine results are exposed in per_engine and popped OUT of
    # engine_status (so the status dict stays JSON-serializable)
    assert out["per_engine"]["perplexity"] == [r]
    assert "results" not in out["engine_status"]["perplexity"]


@pytest.mark.asyncio
async def test_smart_search_wrapper_returns_three_tuple():
    """The legacy smart_search wrapper still returns (message, answer, results)."""
    import monster_search.clients.all_engines as ae
    r = SearchResult(title="t", url="https://e.com", snippet="s", source="x")

    async def _tiered(query, engines, **kwargs):
        return [r], {"perplexity": "ans"}, {"perplexity": {"state": "ok", "count": 1}}

    with patch.object(ae, "get_breaker", return_value=_passthrough_breaker()), \
            patch.object(ae, "tiered_search", side_effect=_tiered), \
            patch.object(ae, "SynthesizerClient") as MockSynth:
        MockSynth.return_value.asearch = AsyncMock(return_value=("", []))
        message, answer, results = await AllEnginesClient().smart_search("q")

    assert isinstance(message, str)
    assert answer == "ans"
    assert results == [r]


# ---------------------------------------------------------------------------
# Live engine-status streaming (on_event)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_event_emits_queued_and_synthesizer():
    """smart_search_rich(on_event=...) announces the full roster upfront (queued,
    incl. synthesizer as tier1) and streams synthesizer running + a terminal."""
    events: list[dict] = []

    async def cb(ev: dict) -> None:
        events.append(ev)

    with _EngineContext():
        rich = await AllEnginesClient().smart_search_rich("mini PC 96GB DDR5", on_event=cb)

    queued = [e for e in events if e.get("type") == "queued"]
    assert len(queued) == 1
    assert queued[0]["engines"].get("synthesizer") == "tier1"
    assert "searxng" in queued[0]["engines"]
    # queued is announced before any engine starts running
    first_running = next(i for i, e in enumerate(events) if e.get("state") == "running")
    assert events.index(queued[0]) < first_running

    synth = [e for e in events if e.get("type") == "engine" and e["engine"] == "synthesizer"]
    synth_states = {e["state"] for e in synth}
    assert "running" in synth_states
    assert synth_states & {"ok", "empty", "failed"}
    # backward-compatible return shape preserved
    assert "engine_status" in rich and "answer" in rich


@pytest.mark.asyncio
async def test_smart_search_rich_without_on_event_unchanged():
    """Omitting on_event leaves the rich return contract intact."""
    with _EngineContext():
        rich = await AllEnginesClient().smart_search_rich("mini PC 96GB DDR5")
    assert set(rich) >= {"message", "answer", "answers", "results", "engine_status", "per_engine"}
