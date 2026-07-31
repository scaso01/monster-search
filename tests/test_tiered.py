"""Tests for tiered execution engine."""

import asyncio

from monster_search._router import QueryCategory
from monster_search._tiered import (
    ENGINE_TIERS,
    tiered_search,
)
from monster_search.models import SearchResult


def _make_result(title: str, source: str = "test") -> SearchResult:
    return SearchResult(
        title=title,
        url=f"https://example.com/{title.replace(' ', '-')}",
        snippet=f"Snippet for {title}",
        source=source,
    )


def _make_engine(results: list[SearchResult] | None = None, fail: bool = False):
    """Create a mock async engine callable (a lambda returning a coroutine factory)."""
    async def _search():
        if fail:
            raise ConnectionError("engine down")
        return results or []
    return lambda: _search()


def _make_tuple_engine(msg: str, results: list[SearchResult]):
    """Engine that returns tuple[str, list[SearchResult]]."""
    async def _search():
        return msg, results
    return lambda: _search()


class TestTieredSearch:
    """Test tier selection logic, result collection, and answer/status capture.

    ``tiered_search`` returns ``(results, answers, status)``.
    """

    def test_tier1_only_when_enough_results(self) -> None:
        """With >= 3 tier1 results and no deep category, only tier1 runs."""
        results = [_make_result(f"r{i}") for i in range(5)]
        engines = {
            "searxng": _make_engine(results),
            "vane": _make_engine([_make_result("should not appear")]),
        }
        out, _answers, status = asyncio.run(tiered_search("test", engines))
        titles = {r.title for r in out}
        # tier2 should NOT have run since tier1 had enough results
        assert "should not appear" not in titles
        assert "r0" in titles
        # vane was built but never ran → reported as skipped
        assert status["vane"]["state"] == "skipped"

    def test_tier2_runs_when_tier1_sparse(self) -> None:
        """If tier1 returns < 3 results, tier2 kicks in."""
        engines = {
            "searxng": _make_engine([_make_result("t1-only")]),
            "vane": _make_engine([_make_result("t2-result")]),
        }
        out, _answers, status = asyncio.run(tiered_search("test", engines))
        titles = {r.title for r in out}
        assert "t1-only" in titles
        assert "t2-result" in titles
        assert status["vane"]["state"] == "ok"

    def test_deep_triggers_tier2(self) -> None:
        """Cumulative deep: include_slow pulls in tier2 AI engines even when
        tier1 already returned enough results (previously deep skipped tier2)."""
        t1 = [_make_result(f"r{i}") for i in range(5)]
        engines = {
            "searxng": _make_engine(t1),
            "vane": _make_engine([_make_result("vane-deep")]),
        }
        out, _answers, status = asyncio.run(
            tiered_search("test", engines, include_slow=True, max_results=20)
        )
        titles = {r.title for r in out}
        assert "vane-deep" in titles
        assert status["vane"]["state"] == "ok"

    def test_tier3_with_include_slow(self) -> None:
        """include_slow=True forces tier3 to run."""
        engines = {
            "searxng": _make_engine([_make_result(f"r{i}") for i in range(5)]),
            "local_researcher": _make_engine([_make_result("slow-result")]),
        }
        out, _answers, _status = asyncio.run(
            tiered_search("test", engines, include_slow=True, max_results=20)
        )
        titles = {r.title for r in out}
        assert "slow-result" in titles

    def test_tier3_with_deep_research_category(self) -> None:
        """DEEP_RESEARCH category triggers tier3."""
        engines = {
            "searxng": _make_engine([_make_result(f"r{i}") for i in range(5)]),
            "local_researcher": _make_engine([_make_result("deep-result")]),
        }
        out, _answers, _status = asyncio.run(tiered_search(
            "test", engines, category=QueryCategory.DEEP_RESEARCH, max_results=20,
        ))
        titles = {r.title for r in out}
        assert "deep-result" in titles

    def test_tier3_skipped_by_default(self) -> None:
        """Without include_slow or deep category, tier3 engines don't run."""
        engines = {
            "searxng": _make_engine([_make_result(f"r{i}") for i in range(5)]),
            "local_researcher": _make_engine([_make_result("should-not-run")]),
        }
        out, _answers, status = asyncio.run(tiered_search("test", engines))
        titles = {r.title for r in out}
        assert "should-not-run" not in titles
        assert status["local_researcher"]["state"] == "skipped"

    def test_no_aggregate_truncation(self) -> None:
        """tiered_search returns the full per-engine pool; it must NOT truncate the
        aggregate to max_results. The old [:max_results] collapsed every engine's
        output to a handful in engine order, starving fusion of all but the first
        engine(s). Capping for display is the caller's job."""
        results = [_make_result(f"r{i}") for i in range(20)]
        engines = {"searxng": _make_engine(results)}
        out, _answers, _status = asyncio.run(tiered_search("test", engines, max_results=3))
        assert len(out) == 20

    def test_engine_failure_graceful(self) -> None:
        """Failed engines are skipped in results but recorded in status with a reason."""
        engines = {
            "searxng": _make_engine(fail=True),
            "marginalia": _make_engine([_make_result("ok")]),
        }
        out, _answers, status = asyncio.run(tiered_search("test", engines))
        assert len(out) >= 1
        assert out[0].title == "ok"
        assert status["searxng"]["state"] == "failed"
        assert status["searxng"]["reason"]  # human-readable failure reason present

    def test_tuple_return_format_captures_answer(self) -> None:
        """Engines returning (message, results) tuples surface BOTH the links and
        the answer text (the answer was previously discarded by the runner)."""
        engines = {
            "perplexity": _make_tuple_engine("summary", [_make_result("ai-result")]),
        }
        # tier1 sparse (only 1 tuple result) → perplexity is tier1
        out, answers, status = asyncio.run(tiered_search("test", engines))
        assert any(r.title == "ai-result" for r in out)
        assert answers["perplexity"] == "summary"
        assert status["perplexity"]["state"] == "ok"
        assert status["perplexity"]["answer"] is True   # produced prose
        assert "ms" in status["perplexity"]             # per-engine latency captured

    def test_answer_engine_ok_with_zero_links(self) -> None:
        """An answer engine returning prose but NO links is 'ok' with count 0 and
        answer=True (so the panel shows 'answer', not a confusing bare 0)."""
        engines = {"perplexity": _make_tuple_engine("just an answer", [])}
        out, answers, status = asyncio.run(tiered_search("test", engines))
        assert out == []
        assert answers["perplexity"] == "just an answer"
        assert status["perplexity"]["state"] == "ok"
        assert status["perplexity"]["count"] == 0
        assert status["perplexity"]["answer"] is True

    def test_status_records_latency_for_all_states(self) -> None:
        """Every engine that ran (ok/empty/failed) carries a numeric ms."""
        engines = {
            "searxng": _make_engine([_make_result("r")]),  # ok
            "marginalia": _make_engine([]),                # empty
            "mwmbl": _make_engine(fail=True),              # failed
        }
        _out, _answers, status = asyncio.run(tiered_search("test", engines))
        assert status["searxng"]["state"] == "ok"
        assert status["marginalia"]["state"] == "empty"
        assert status["mwmbl"]["state"] == "failed"
        for name in ("searxng", "marginalia", "mwmbl"):
            assert isinstance(status[name]["ms"], int)

    def test_status_carries_raw_per_engine_results(self) -> None:
        """Each ok/empty engine's status carries its raw item list under 'results'
        (piggybacked for the dashboard's per-source view); count == len(results)."""
        engines = {
            "searxng": _make_engine([_make_result("a"), _make_result("b")]),
            "marginalia": _make_engine([]),
        }
        _out, _answers, status = asyncio.run(tiered_search("test", engines))
        assert [r.title for r in status["searxng"]["results"]] == ["a", "b"]
        assert status["searxng"]["count"] == len(status["searxng"]["results"])
        assert status["marginalia"]["results"] == []


def test_timed_call_returns_elapsed_and_swallows_exceptions() -> None:
    """timed_call returns (result, exc, ms) and never raises."""
    from monster_search._breaker import timed_call

    async def ok():
        return "X"

    async def boom():
        raise ValueError("nope")

    res, exc, ms = asyncio.run(timed_call(ok()))
    assert res == "X" and exc is None and isinstance(ms, int) and ms >= 0
    res2, exc2, ms2 = asyncio.run(timed_call(boom()))
    assert res2 is None and isinstance(exc2, ValueError) and isinstance(ms2, int)

    def test_answers_only_for_tuple_engines(self) -> None:
        """List-returning engines contribute no answer; tuple engines do."""
        engines = {
            "searxng": _make_engine([_make_result("link")]),
            "perplexity": _make_tuple_engine("ai answer", [_make_result("src")]),
        }
        _out, answers, _status = asyncio.run(tiered_search("test", engines))
        assert "searxng" not in answers
        assert answers["perplexity"] == "ai answer"

    def test_empty_answer_string_not_captured(self) -> None:
        """An engine returning ('', results) (e.g. news) contributes no answer
        but is still 'ok' because it returned links."""
        engines = {
            "searxng": _make_engine([_make_result("x")]),
            "news": _make_tuple_engine("", [_make_result("n")]),
        }
        _out, answers, status = asyncio.run(tiered_search("test", engines))
        assert "news" not in answers
        assert status["news"]["state"] == "ok"

    def test_empty_engines_dict(self) -> None:
        """No engines → empty results, answers, status."""
        out, answers, status = asyncio.run(tiered_search("test", {}))
        assert out == []
        assert answers == {}
        assert status == {}

    def test_category_routes_tier2_engines(self) -> None:
        """Category routing adds specific tier2 engines even when tier1 has results."""
        t1_results = [_make_result(f"r{i}") for i in range(5)]
        engines = {
            "searxng": _make_engine(t1_results),
            "fyin": _make_engine([_make_result("fyin-result")]),
        }
        # NEWS category routes to news, gnews, searxng — none in tier2.
        # But fyin is tier2 and NOT in the category routing, so it shouldn't run.
        out, _answers, status = asyncio.run(tiered_search(
            "test", engines, category=QueryCategory.NEWS,
        ))
        titles = {r.title for r in out}
        assert "fyin-result" not in titles
        assert status["fyin"]["state"] == "skipped"


class TestEngineTiers:
    """Validate tier definitions."""

    def test_no_engine_in_multiple_tiers(self) -> None:
        """Each engine should be in exactly one tier."""
        all_engines: list[str] = []
        for engines in ENGINE_TIERS.values():
            all_engines.extend(engines)
        assert len(all_engines) == len(set(all_engines)), "duplicate engine across tiers"

    def test_tier_names(self) -> None:
        assert set(ENGINE_TIERS.keys()) == {"tier1_fast", "tier2_medium", "tier3_slow"}


def _slow_engine(seconds: float, results: list[SearchResult] | None = None):
    """Mock engine that sleeps before returning (for timeout tests)."""
    async def _search():
        await asyncio.sleep(seconds)
        return results or []
    return lambda: _search()


class TestEventStreaming:
    """Live ``on_event`` progress callback — optional, off by default."""

    def test_on_event_none_is_default(self) -> None:
        """Omitting on_event (the default) leaves the legacy path unchanged."""
        engines = {"searxng": _make_engine([_make_result("r")])}
        out, answers, status = asyncio.run(tiered_search("test", engines))
        assert [r.title for r in out] == ["r"]
        assert status["searxng"]["state"] == "ok"

    def test_emits_tier_start_and_per_engine_events(self) -> None:
        """A tier 'start' event fires, then each engine emits running + a terminal
        state. Done events carry count + ms and DROP the heavy raw 'results'."""
        events: list[dict] = []

        async def cb(ev: dict) -> None:
            events.append(ev)

        engines = {
            "searxng": _make_engine([_make_result("a"), _make_result("b")]),
            "marginalia": _make_engine([]),          # empty
            "mwmbl": _make_engine(fail=True),        # failed
        }
        asyncio.run(tiered_search("test", engines, on_event=cb))

        assert {"type": "tier", "tier": "tier1", "state": "start"} in events
        engine_evs = [e for e in events if e["type"] == "engine"]
        states = {(e["engine"], e["state"]) for e in engine_evs}
        assert ("searxng", "running") in states and ("searxng", "ok") in states
        assert ("marginalia", "empty") in states
        assert ("mwmbl", "failed") in states
        # terminal events: metadata only, never the raw SearchResult payload
        done = [e for e in engine_evs if e["state"] != "running"]
        for e in done:
            assert "results" not in e
            assert "count" in e and "ms" in e

    def test_running_precedes_done_per_engine(self) -> None:
        """For a given engine its 'running' event comes before its terminal one."""
        events: list[dict] = []

        async def cb(ev: dict) -> None:
            events.append(ev)

        engines = {"searxng": _make_engine([_make_result("a")])}
        asyncio.run(tiered_search("test", engines, on_event=cb))
        seq = [e["state"] for e in events if e.get("engine") == "searxng"]
        assert seq.index("running") < seq.index("ok")

    def test_tier3_timeout_marks_engine_and_emits(self, monkeypatch) -> None:
        """A tier3 engine that blows the cap is abandoned, reported 'timeout',
        and its results never leak into the pool."""
        import monster_search._tiered as t
        monkeypatch.setattr(t, "TIER3_TIMEOUT_S", 0.05)

        events: list[dict] = []

        async def cb(ev: dict) -> None:
            events.append(ev)

        engines = {
            "searxng": _make_engine([_make_result(f"r{i}") for i in range(5)]),
            "local_researcher": _slow_engine(5.0, [_make_result("never")]),
        }
        out, _answers, status = asyncio.run(
            tiered_search("test", engines, include_slow=True, on_event=cb)
        )
        assert status["local_researcher"]["state"] == "timeout"
        assert "never" not in {r.title for r in out}
        assert any(
            e.get("engine") == "local_researcher" and e.get("state") == "timeout"
            for e in events
        )
