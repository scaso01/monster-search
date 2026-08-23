"""CLI surface tests: flags, category aliases and the watch subcommands.

test_cli.py covers the default search path and the error handling. This file
covers the rest of the documented command line, which previously had none.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

import monster_search
from monster_search.cli import ENGINE_CATEGORIES, main
from monster_search.clients.all_engines import AllEnginesClient
from monster_search.models import SearchResult


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Stop main() reading a real .env, which would vary by machine."""
    monkeypatch.setattr("monster_search.cli.load_dotenv", lambda *a, **kw: False)


def _result(source: str, n: int = 1) -> list[SearchResult]:
    return [
        SearchResult(
            title=f"{source} {i}",
            url=f"https://{source}.example/{i}",
            snippet="snippet",
            source=source,
        )
        for i in range(n)
    ]


def _smart_mock(n: int = 1) -> AsyncMock:
    return AsyncMock(return_value=("msg", "", _result("searxng", n)))


# --- smart search flags -------------------------------------------------


def test_deep_requests_slow_engines():
    """--deep is the only way to reach tier2/tier3, so it must be forwarded."""
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--deep", "rust async"])

    assert smart.call_args.kwargs["include_slow"] is True


def test_default_search_does_not_request_slow_engines():
    """Without --deep the slow tiers stay off."""
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["rust async"])

    assert smart.call_args.kwargs["include_slow"] is False


def test_engine_all_always_includes_slow():
    """`--engine all` means a full sweep whether or not --deep was passed."""
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--engine", "all", "rust async"])

    assert smart.call_args.kwargs["include_slow"] is True


def test_no_fuse_disables_fusion():
    """--no-fuse must reach the client as fuse=False."""
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--no-fuse", "rust async"])

    assert smart.call_args.kwargs["fuse"] is False


def test_fusion_is_on_by_default():
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["rust async"])

    assert smart.call_args.kwargs["fuse"] is True


def test_max_results_is_forwarded():
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--max-results", "17", "rust async"])

    assert smart.call_args.kwargs["max_results"] == 17


def test_max_results_caps_the_human_readable_output(capsys):
    """The brief output is truncated even when the engine returns more."""
    smart = AsyncMock(return_value=("msg", "", _result("searxng", 10)))
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--max-results", "3", "rust async"])

    out = capsys.readouterr().out
    assert "searxng.example/2" in out
    assert "searxng.example/3" not in out


def test_query_words_are_joined():
    """argparse collects the query as a list; it must be rejoined verbatim."""
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["rust", "async", "patterns"])

    assert smart.call_args.args[0] == "rust async patterns"


# --- SearXNG passthrough flags ------------------------------------------


@respx.mock
def test_time_range_reaches_searxng():
    route = respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    main(["--engine", "searxng", "--time-range", "week", "news"])

    assert "time_range=week" in str(route.calls[0].request.url)


@respx.mock
def test_category_reaches_searxng():
    route = respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    main(["--engine", "searxng", "--category", "science", "fusion"])

    assert "categories=science" in str(route.calls[0].request.url)


# --- JSON output --------------------------------------------------------


def test_json_includes_answer_when_present(capsys):
    """An answer paragraph must survive into the JSON payload."""
    smart = AsyncMock(return_value=("msg", "The answer.", _result("searxng")))
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--json", "rust async"])

    data = json.loads(capsys.readouterr().out)
    assert data["answer"] == "The answer."
    assert data["message"] == "msg"


def test_json_omits_answer_when_absent(capsys):
    smart = _smart_mock()
    with patch.object(AllEnginesClient, "smart_search", smart):
        main(["--json", "rust async"])

    assert "answer" not in json.loads(capsys.readouterr().out)


# --- argument validation ------------------------------------------------


def test_missing_query_is_an_error(capsys):
    with pytest.raises(SystemExit):
        main([])

    assert "query is required" in capsys.readouterr().err


def test_benchmark_without_query_is_an_error(capsys):
    with pytest.raises(SystemExit):
        main(["--benchmark"])

    assert "query is required" in capsys.readouterr().err


def test_unknown_engine_is_rejected():
    """argparse `choices` should refuse an engine that does not exist."""
    with pytest.raises(SystemExit):
        main(["--engine", "not-a-real-engine", "q"])


# --- gated engines ------------------------------------------------------


def test_semantic_scholar_without_a_key_exits_cleanly(capsys):
    """The gate must explain itself without naming anyone's local filesystem."""
    with pytest.raises(SystemExit):
        main(["--engine", "semantic_scholar", "attention"])

    err = capsys.readouterr().err
    assert "MONSTER_SEMANTIC_SCHOLAR_API_KEY" in err
    assert "~/Projects" not in err


def test_grepapp_disabled_exits_cleanly(capsys):
    """Same for grepapp, which is off by default."""
    with pytest.raises(SystemExit):
        main(["--engine", "grepapp", "func main"])

    err = capsys.readouterr().err
    assert "MONSTER_GREPAPP_ENABLED" in err
    assert "~/Projects" not in err


# The shape of a developer path, not one author's name. Spelling a real username
# here would publish the very thing this test exists to keep out, and matching the
# shape catches any contributor's home directory rather than only the first one.
DEVELOPER_PATH = re.compile(r"~/Projects|[Uu]sers[\\/][A-Za-z0-9._-]+[\\/]")


def test_no_developer_paths_in_the_shipped_package():
    """A public package must not tell users to edit a path only its author has."""
    root = Path(monster_search.__file__).parent
    offenders = sorted(
        path.name
        for path in root.rglob("*.py")
        if DEVELOPER_PATH.search(path.read_text(encoding="utf-8"))
    )

    assert offenders == []


# --- benchmark ----------------------------------------------------------


def test_benchmark_single_engine_runs_only_that_engine(capsys):
    with patch("monster_search.benchmark.run_benchmark", return_value=[]) as run:
        with patch("monster_search.benchmark.format_table", return_value="TABLE"):
            main(["--benchmark", "--engine", "searxng", "rust"])

    assert run.call_args.kwargs["engines"] == ["searxng"]
    assert "TABLE" in capsys.readouterr().out


def test_benchmark_without_engine_runs_the_full_set():
    from monster_search.benchmark import BENCHMARKABLE_ENGINES

    with patch("monster_search.benchmark.run_benchmark", return_value=[]) as run:
        with patch("monster_search.benchmark.format_table", return_value=""):
            main(["--benchmark", "rust"])

    assert run.call_args.kwargs["engines"] == BENCHMARKABLE_ENGINES


def test_benchmark_with_a_category_runs_the_full_set():
    """A category alias is not a single engine, so it benchmarks everything."""
    from monster_search.benchmark import BENCHMARKABLE_ENGINES

    with patch("monster_search.benchmark.run_benchmark", return_value=[]) as run:
        with patch("monster_search.benchmark.format_table", return_value=""):
            main(["--benchmark", "--engine", "academic", "attention"])

    assert run.call_args.kwargs["engines"] == BENCHMARKABLE_ENGINES


# --- category aliases ---------------------------------------------------


SHOPPING_CLIENTS = {
    "slickdeals": "monster_search.clients.slickdeals.SlickdealsClient",
    "cheapshark": "monster_search.clients.cheapshark.CheapSharkClient",
    "deals_rss": "monster_search.clients.deals_rss.DealsRSSClient",
    "priceghost": "monster_search.clients.priceghost.PriceGhostClient",
    "amazon_deals": "monster_search.clients.amazon_deals.AmazonDealsClient",
    "newegg": "monster_search.clients.newegg.NeweggClient",
}


def test_shopping_alias_excludes_the_retired_searxng_shopping_engine():
    """SearXNG's shopping category resolves to geizhals alone, which answers 403
    behind a Cloudflare challenge that Crawl4AI's headless browser does not
    clear either — so it occupied a slot in every shopping sweep and returned
    nothing. It stays selectable via --engine for anyone geizhals answers."""
    from monster_search.cli import ENGINE_CHOICES

    assert "searxng_shopping" not in ENGINE_CATEGORIES["shopping"]
    assert "searxng_shopping" in ENGINE_CHOICES


def test_shopping_alias_dispatches_every_engine_it_lists(capsys):
    """Every engine in the alias must actually run, not fall through to [].

    A name listed in ENGINE_CATEGORIES with no matching branch in _run_category
    returns an empty list and is still reported as succeeded, so only counting
    results catches it.
    """
    with _patched(SHOPPING_CLIENTS):
        main(["--json", "--engine", "shopping", "laptop"])

    data = json.loads(capsys.readouterr().out)
    got = {r["source"] for r in data["results"]}
    assert got == set(SHOPPING_CLIENTS)


def test_every_category_alias_is_non_empty():
    """An alias that resolves to nothing would silently return no results."""
    for name, engines in ENGINE_CATEGORIES.items():
        assert engines, f"category alias {name} lists no engines"


class _patched:
    """Patch a mapping of engine name -> client dotted path onto async stubs."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self._patchers: list = []

    def __enter__(self):
        for engine, dotted in self._mapping.items():
            stub = AsyncMock(return_value=_result(engine))
            patcher = patch(f"{dotted}.asearch", stub)
            patcher.start()
            self._patchers.append(patcher)
        return self

    def __exit__(self, *exc):
        for patcher in self._patchers:
            patcher.stop()
        return False


# --- watch subcommands --------------------------------------------------


WATCH_CLIENT = "monster_search.clients.changedetection_client.ChangeDetectionClient"


def test_watch_add(capsys):
    with patch(f"{WATCH_CLIENT}.add_watch", return_value={"uuid": "abc"}) as add:
        main(["watch", "add", "https://example.com", "--tag", "news"])

    add.assert_called_once_with("https://example.com", tag="news")
    assert "abc" in capsys.readouterr().out


def test_watch_list(capsys):
    watches = [{"uuid": "u1", "url": "https://a.example", "tag": "news"}]
    with patch(f"{WATCH_CLIENT}.list_watches", return_value=watches):
        main(["watch", "list"])

    out = capsys.readouterr().out
    assert "u1" in out
    assert "https://a.example" in out
    assert "[news]" in out


def test_watch_list_filters_by_tag():
    with patch(f"{WATCH_CLIENT}.list_watches", return_value=[]) as lst:
        main(["watch", "list", "--tag", "news"])

    lst.assert_called_once_with(tag="news")


def test_watch_list_empty(capsys):
    with patch(f"{WATCH_CLIENT}.list_watches", return_value=[]):
        main(["watch", "list"])

    assert "No watches found." in capsys.readouterr().out


def test_watch_check(capsys):
    with patch(f"{WATCH_CLIENT}.get_latest", return_value="page text") as get:
        main(["watch", "check", "uuid-1"])

    get.assert_called_once_with("uuid-1")
    assert "page text" in capsys.readouterr().out


def test_watch_diff(capsys):
    with patch(f"{WATCH_CLIENT}.get_diff", return_value="- old\n+ new") as diff:
        main(["watch", "diff", "uuid-1"])

    diff.assert_called_once_with("uuid-1")
    assert "+ new" in capsys.readouterr().out


def test_watch_remove_success(capsys):
    with patch(f"{WATCH_CLIENT}.remove_watch", return_value=True):
        main(["watch", "remove", "uuid-1"])

    assert "Removed." in capsys.readouterr().out


def test_watch_remove_failure(capsys):
    """A failed removal must not claim success."""
    with patch(f"{WATCH_CLIENT}.remove_watch", return_value=False):
        main(["watch", "remove", "uuid-1"])

    assert "Failed to remove." in capsys.readouterr().out


def test_watch_without_an_action_prints_help(capsys):
    main(["watch"])

    assert "usage" in capsys.readouterr().out.lower()
