"""Fact-check evidence gathering, quote verification and verdict rules.

The property that matters most: an unchecked claim can never come back TRUE.
No evidence exits non-zero; an invented or unverifiable quote does not count;
TRUE needs verified support from two independent domains and no refutation.
"""

from __future__ import annotations

import json

import pytest

from monster_search.cli import main
from monster_search.clients.brave_html import BraveBlockedError, BraveHtmlClient, parse_results
from monster_search.clients import factcheck as fc
from monster_search.config import Config
from monster_search.models import SearchResult


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setattr("monster_search.cli.load_dotenv", lambda *a, **kw: False)


def _r(url: str, title: str = "t", snippet: str = "snippet text") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, source="x")


def _engines(monkeypatch, searxng=(), ddg=(), gnews=(), brave=(), perplexity=(), fail=()):
    """Stub every engine; names in ``fail`` raise instead of answering."""

    def make(name, results):
        def search(self, query, **kw):
            if name in fail:
                raise RuntimeError(f"{name} down")
            return list(results)
        return search

    monkeypatch.setattr(fc.SearXNGClient, "search", make("searxng", searxng))
    monkeypatch.setattr(fc.DdgBrowserClient, "search", make("ddg", ddg))
    monkeypatch.setattr(fc.GNewsClient, "search", make("gnews", gnews))
    monkeypatch.setattr(fc.BraveHtmlClient, "search", make("brave", brave))
    ppx = make("perplexity", perplexity)
    monkeypatch.setattr(fc.PerplexityClient, "search", lambda self, q, **kw: ("AI verdict text", ppx(self, q)))


def _pages(monkeypatch, texts: dict[str, str], fail=()):
    def search(self, url, **kw):
        if url in fail:
            raise RuntimeError("fetch failed")
        return texts.get(url, ""), []
    monkeypatch.setattr(fc.Crawl4AIClient, "search", search)


# --- helpers -----------------------------------------------------------------

def test_domain_of_collapses_subdomains_and_country_suffixes():
    assert fc.domain_of("https://en.wikipedia.org/wiki/X") == "wikipedia.org"
    assert fc.domain_of("https://simple.wikipedia.org/wiki/X") == "wikipedia.org"
    assert fc.domain_of("https://www.bbc.co.uk/news/1") == "bbc.co.uk"
    assert fc.domain_of("https://www.sec.gov/x") == "sec.gov"


def test_clean_markdown_strips_links_footnotes_and_images():
    md = ('Ambedkar[[a]](https://w/x#cite) was Minister of [Labour](https://w/Ministry_\\(India\\) '
          '"Ministry (India)") ![pic](https://i/p.png) in 1942.')
    assert fc.clean_markdown(md) == "Ambedkar was Minister of Labour in 1942."


@pytest.mark.parametrize("quote,ok", [
    ("and in the United States in July 2011", True),
    ("AND in the   united states in july 2011", True),             # case and spacing
    ("“Spotify” launched in several European markets", True),  # curly quotes
    ("launched in several ... in July 2011", True),                   # ellipsis fragments in order
    ("in July 2011 ... launched in several", False),                  # fragments out of order
    ("launched in the United States in July 2009", False),            # altered fact
    ("July 2011", False),                                            # too short to prove anything
])
def test_quote_in_text(quote, ok):
    text = '"Spotify" launched in several European markets in October 2008 and in the United States in July 2011.'
    assert fc.quote_in_text(quote, text) is ok


def test_excerpts_prefer_passages_about_the_claim():
    text = "\n\n".join([
        "Navigation menu and other site chrome that has nothing to do with anything at all here.",
        "Spotify launched in the United States in July 2011 after years of licensing talks with labels.",
    ])
    assert fc.excerpts_for("Spotify launched in the United States", text) == [
        "Spotify launched in the United States in July 2011 after years of licensing talks with labels."
    ]


# --- gather ------------------------------------------------------------------

def test_no_results_raises_no_evidence(monkeypatch):
    _engines(monkeypatch)
    with pytest.raises(fc.NoEvidenceError) as exc:
        fc.gather("claim", Config())
    assert exc.value.all_failed is False


def test_every_engine_failing_is_reported_as_infrastructure(monkeypatch):
    _engines(monkeypatch, fail=("searxng", "ddg", "gnews", "brave", "perplexity"))
    with pytest.raises(fc.NoEvidenceError) as exc:
        fc.gather("claim", Config())
    assert exc.value.all_failed is True
    assert exc.value.engines["ddg"]["status"] == "error"


def test_transient_engine_error_is_retried_once(monkeypatch):
    _engines(monkeypatch)
    calls = []
    def flaky(self, query, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return [_r("https://a.com/1"), _r("https://b.com/1")]
    monkeypatch.setattr(fc.DdgBrowserClient, "search", flaky)
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert ev["engines"]["ddg"] == {"status": "ok", "count": 2}


def test_one_engine_down_is_reported_not_fatal(monkeypatch):
    _engines(monkeypatch, ddg=[_r("https://a.com/1")], fail=("searxng",))
    _pages(monkeypatch, {"https://a.com/1": "Some page text long enough to count as a real paragraph of prose here."})
    ev = fc.gather("claim", Config())
    assert ev["engines"]["searxng"]["status"] == "error"
    assert ev["engines"]["ddg"] == {"status": "ok", "count": 1}
    assert [s["url"] for s in ev["sources"]] == ["https://a.com/1"]


def test_ai_verdict_sites_and_unresolved_gnews_are_excluded(monkeypatch):
    _engines(
        monkeypatch,
        ddg=[_r("https://isthisbs.org/health/x"), _r("https://lenz.io/c/abc")],
        gnews=[_r("https://news.google.com/rss/articles/CBMi")],
    )
    with pytest.raises(fc.NoEvidenceError):
        fc.gather("claim", Config(), fetch_pages=False)


def test_exclusions_are_listed_with_reasons(monkeypatch):
    _engines(monkeypatch, ddg=[_r("https://isthisbs.org/x"), _r("https://real.com/a"),
                               _r("https://www.mediamass.net/people/x/deathhoax.html"), _r("https://b.com/a")])
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert ev["excluded"] == [
        {"url": "https://isthisbs.org/x", "why": "publishes another AI's verdict (circular)"},
        {"url": "https://www.mediamass.net/people/x/deathhoax.html", "why": "auto-generated celebrity hoax page"},
    ]


def test_ranking_puts_multi_engine_hits_first_then_alternates(monkeypatch):
    _engines(
        monkeypatch,
        searxng=[_r("https://s.com/1"), _r("https://s.com/2"), _r("https://both.com/x")],
        ddg=[_r("https://d.com/1"), _r("https://www.both.com/x/")],
    )
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert [s["url"] for s in ev["sources"]] == [
        "https://both.com/x", "https://s.com/1", "https://d.com/1", "https://s.com/2",
    ]
    assert ev["sources"][0]["found_by"] == ["searxng", "ddg"]


def test_fetch_failure_falls_back_to_snippet_and_is_recorded(monkeypatch):
    _engines(monkeypatch, ddg=[_r("https://a.com/1", snippet="the snippet")])
    _pages(monkeypatch, {}, fail=("https://a.com/1",))
    src = fc.gather("claim", Config())["sources"][0]
    assert src["text_source"] == "snippet"
    assert src["text"] == "the snippet"
    assert "fetch failed" in src["fetch_error"]


def test_reader_view_hides_full_text():
    ev = {"evidence_id": "e", "claim": "c", "engines": {}, "excluded": [],
          "sources": [{"n": 1, "text": "FULL", "excerpts": ["ex"]}]}
    assert fc.reader_view(ev)["sources"] == [{"n": 1, "excerpts": ["ex"]}]


# --- Brave fallback ------------------------------------------------------------

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def test_brave_parser_reads_a_real_results_page():
    page = (FIXTURES / "brave_results.html").read_text(encoding="utf-8")
    results = parse_results(page)
    assert [r.url for r in results] == [
        "https://en.wikipedia.org/wiki/Eiffel_Tower",
        "https://www.toureiffel.paris/en/the-monument/key-figures",
    ]
    assert results[0].title == "Eiffel Tower - Wikipedia"
    assert results[0].snippet.startswith("The Eiffel Tower is the most visited monument")
    assert "3 days ago" not in results[0].snippet and "<" not in results[0].snippet


def test_brave_is_not_spent_when_core_engines_suffice(monkeypatch):
    called = []
    _engines(monkeypatch, ddg=[_r("https://a.com/1"), _r("https://b.com/1")])
    monkeypatch.setattr(fc.BraveHtmlClient, "search", lambda self, q, **kw: called.append(q) or [])
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert called == []
    assert ev["engines"]["brave"] == {"status": "not needed"}
    assert ev["engines"]["perplexity"] == {"status": "not needed"}


def test_brave_fills_in_when_core_engines_find_too_little(monkeypatch):
    _engines(monkeypatch, ddg=[_r("https://a.com/1")], brave=[_r("https://b.com/1")])
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert ev["engines"]["brave"] == {"status": "ok", "count": 1}
    assert {s["url"] for s in ev["sources"]} == {"https://a.com/1", "https://b.com/1"}


def test_perplexity_fallback_uses_cited_pages_never_its_answer(monkeypatch):
    _engines(monkeypatch, perplexity=[_r("https://p.com/1", snippet="page snippet")], fail=("brave",))
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert ev["engines"]["perplexity"] == {"status": "ok", "count": 1}
    assert ev["engines"]["brave"]["status"] == "error"
    assert [s["url"] for s in ev["sources"]] == ["https://p.com/1"]
    assert "AI verdict text" not in json.dumps(ev)


def test_brave_missing_dependency_is_skipped_not_fatal(monkeypatch):
    _engines(monkeypatch, ddg=[_r("https://a.com/1")])
    def no_curl(self, q, **kw):
        raise ImportError("No module named 'curl_cffi'")
    monkeypatch.setattr(fc.BraveHtmlClient, "search", no_curl)
    ev = fc.gather("claim", Config(), fetch_pages=False)
    assert ev["engines"]["brave"]["status"] == "skipped"
    assert len(ev["sources"]) == 1


class _Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_brave_client_rotates_exits_on_429_and_resolves_dns_remotely(monkeypatch):
    import curl_cffi.requests as cr
    page = (FIXTURES / "brave_results.html").read_text(encoding="utf-8")
    seen = []
    def get(url, **kw):
        seen.append(kw["proxy"])
        return _Resp(429) if len(seen) == 1 else _Resp(200, page)
    monkeypatch.setattr(cr, "get", get)
    cfg = Config(socks_proxies=("socks5://h:1", "socks5://h:2"))
    assert len(BraveHtmlClient(cfg).search("q")) == 2
    assert seen == ["socks5h://h:1", "socks5h://h:2"]


def test_brave_client_raises_when_every_exit_is_blocked(monkeypatch):
    import curl_cffi.requests as cr
    monkeypatch.setattr(cr, "get", lambda url, **kw: _Resp(429))
    with pytest.raises(BraveBlockedError):
        BraveHtmlClient(Config(socks_proxies=("socks5://h:1", "socks5://h:2"))).search("q")


# --- verify ------------------------------------------------------------------

TEXT_A = "Spotify launched in the United States in July 2011 after long negotiations."
TEXT_B = "The service reached American listeners in July 2011, according to the company."
TEXT_C = "Spotify first launched in the United States in 2009, one report wrongly claimed."


def _evidence(*sources) -> dict:
    return {
        "evidence_id": "ev1",
        "claim": "Spotify launched in the US in July 2011",
        "engines": {},
        "sources": [{"n": i, "url": url, "domain": fc.domain_of(url), "text": text}
                    for i, (url, text) in enumerate(sources, start=1)],
    }


def _labels(*labels) -> dict:
    return {"evidence_id": "ev1", "labels": [
        {"source": n, "stance": stance, "quote": quote} for n, stance, quote in labels
    ]}


def test_true_needs_two_independent_domains():
    ev = _evidence(("https://a.com/1", TEXT_A), ("https://b.org/2", TEXT_B))
    out = fc.verify(ev, _labels((1, "supports", "launched in the United States in July 2011"),
                                (2, "supports", "reached American listeners in July 2011")))
    assert out["verdict"] == "TRUE"


def test_two_sources_on_one_domain_are_not_enough():
    ev = _evidence(("https://en.wikipedia.org/a", TEXT_A), ("https://simple.wikipedia.org/b", TEXT_B))
    out = fc.verify(ev, _labels((1, "supports", "launched in the United States in July 2011"),
                                (2, "supports", "reached American listeners in July 2011")))
    assert out["verdict"] == "INCONCLUSIVE"
    assert out["reason"] == "only one independent source takes a side"


def test_invented_quote_cannot_make_a_claim_true():
    ev = _evidence(("https://a.com/1", TEXT_A), ("https://b.org/2", TEXT_B))
    out = fc.verify(ev, _labels((1, "supports", "launched in the United States in July 2011"),
                                (2, "supports", "Spotify officially confirmed the July 2011 US launch date")))
    assert out["verdict"] == "INCONCLUSIVE"
    assert out["rejected_quotes"][0]["why"] == "quote not found in source text"


def test_false_needs_two_refuting_domains_and_no_support():
    ev = _evidence(("https://a.com/1", TEXT_A), ("https://b.org/2", TEXT_B))
    out = fc.verify(ev, _labels((1, "refutes", "launched in the United States in July 2011"),
                                (2, "refutes", "reached American listeners in July 2011")))
    assert out["verdict"] == "FALSE"


def test_conflicting_sources_are_inconclusive():
    ev = _evidence(("https://a.com/1", TEXT_A), ("https://b.org/2", TEXT_B), ("https://c.net/3", TEXT_C))
    out = fc.verify(ev, _labels((1, "supports", "launched in the United States in July 2011"),
                                (2, "supports", "reached American listeners in July 2011"),
                                (3, "refutes", "first launched in the United States in 2009")))
    assert out["verdict"] == "INCONCLUSIVE"
    assert out["reason"] == "sources conflict"


def test_all_irrelevant_is_inconclusive():
    ev = _evidence(("https://a.com/1", TEXT_A))
    out = fc.verify(ev, _labels((1, "irrelevant", "")))
    assert out["verdict"] == "INCONCLUSIVE"
    assert out["reason"] == "no verified evidence either way"


def test_labels_for_a_different_claim_are_refused():
    ev = _evidence(("https://a.com/1", TEXT_A))
    with pytest.raises(fc.VerifyInputError, match="labels are for evidence"):
        fc.verify(ev, {"evidence_id": "other", "labels": [{"source": 1, "stance": "irrelevant"}]})


def test_every_source_must_be_labeled():
    ev = _evidence(("https://a.com/1", TEXT_A), ("https://b.org/2", TEXT_B))
    with pytest.raises(fc.VerifyInputError, match="missing \\[2\\]"):
        fc.verify(ev, _labels((1, "irrelevant", "")))


@pytest.mark.parametrize("label,match", [
    ({"source": 9, "stance": "supports", "quote": "x"}, "unknown source"),
    ({"source": 1, "stance": "agrees", "quote": "x"}, "stance must be one of"),
])
def test_malformed_labels_are_refused(label, match):
    with pytest.raises(fc.VerifyInputError, match=match):
        fc.verify(_evidence(("https://a.com/1", TEXT_A)), {"evidence_id": "ev1", "labels": [label]})


# --- CLI ---------------------------------------------------------------------

def test_cli_gather_exits_3_when_no_evidence(monkeypatch, capsys):
    _engines(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        main(["factcheck", "gather", "some", "claim"])
    assert exc.value.code == 3
    assert "UNCHECKED" in capsys.readouterr().err


def test_cli_gather_exits_1_when_every_engine_fails(monkeypatch):
    _engines(monkeypatch, fail=("searxng", "ddg", "gnews", "brave", "perplexity"))
    with pytest.raises(SystemExit) as exc:
        main(["factcheck", "gather", "claim"])
    assert exc.value.code == 1


def test_cli_gather_then_verify_round_trip(monkeypatch, capsys, tmp_path):
    _engines(monkeypatch, ddg=[_r("https://a.com/1"), _r("https://b.org/2")])
    _pages(monkeypatch, {"https://a.com/1": TEXT_A, "https://b.org/2": TEXT_B})
    ev_file = tmp_path / "ev.json"
    main(["factcheck", "gather", "Spotify launched in the US in July 2011", "--out", str(ev_file)])
    view = json.loads(capsys.readouterr().out)
    assert view["evidence_file"] == str(ev_file)
    assert all("text" not in s for s in view["sources"])

    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"evidence_id": view["evidence_id"], "labels": [
        {"source": 1, "stance": "supports", "quote": "launched in the United States in July 2011"},
        {"source": 2, "stance": "supports", "quote": "reached American listeners in July 2011"},
    ]}), encoding="utf-8")
    main(["factcheck", "verify", str(ev_file), str(labels)])
    assert json.loads(capsys.readouterr().out)["verdict"] == "TRUE"


def test_cli_verify_exits_2_on_bad_labels(tmp_path, capsys):
    ev_file = tmp_path / "ev.json"
    ev_file.write_text(json.dumps(_evidence(("https://a.com/1", TEXT_A))), encoding="utf-8")
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"evidence_id": "wrong", "labels": []}), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["factcheck", "verify", str(ev_file), str(labels)])
    assert exc.value.code == 2


# --- synth CLI fixes ---------------------------------------------------------

def test_synth_answer_lands_in_answer_and_max_results_is_forwarded(monkeypatch, capsys):
    seen = {}

    def search(self, query, *, deep=False, max_sources=5):
        seen["max_sources"] = max_sources
        return "It launched in July 2011 [1].", [_r("https://a.com/1")]

    monkeypatch.setattr("monster_search.clients.synthesizer.SynthesizerClient.search", search)
    main(["--engine", "synth", "--json", "--max-results", "8", "q"])
    out = json.loads(capsys.readouterr().out)
    assert out["answer"] == "It launched in July 2011 [1]."
    assert "message" not in out
    assert seen["max_sources"] == 8


def test_synth_with_no_evidence_exits_3(monkeypatch, capsys):
    monkeypatch.setattr(
        "monster_search.clients.synthesizer.SynthesizerClient.search",
        lambda self, query, **kw: ("", []),
    )
    with pytest.raises(SystemExit) as exc:
        main(["--engine", "synth", "q"])
    assert exc.value.code == 3
    assert "no web evidence" in capsys.readouterr().err
