"""Tiered execution engine — runs search engines in priority tiers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from monster_search._breaker import failure_reason, timed_call
from monster_search._router import QueryCategory
from monster_search.models import SearchResult


ENGINE_TIERS: dict[str, list[str]] = {
    "tier1_fast": [
        "searxng", "searxng_shopping", "marginalia", "mwmbl", "news", "gnews", "openalex",
        "archive_org", "osv", "deps", "whodat", "zoekt",
        "arxiv", "semantic_scholar", "meilisearch",
        "perplexity", "synthesizer",
        "youtube", "grepapp", "github_code",
        "hackernews", "huggingface", "reddit", "github_repos",
        "slickdeals", "cheapshark", "deals_rss",
        "priceghost",
    ],
    "tier2_medium": [
        # Browser-rendered: ~3s, but the only path DuckDuckGo accepts.
        "ddg",
        "vane", "khoj", "fyin",
        "amazon_deals", "newegg",
    ],
    "tier3_slow": [
        "local_researcher",
    ],
}

# Categories that should always include their routed engines regardless of tier
_DEEP_CATEGORIES = {QueryCategory.DEEP_RESEARCH}

# Hard ceiling on the slow tier (tier3 = local_researcher, which can iterate for
# minutes). Without it a hung deep run blocks the whole request indefinitely (a
# real deep run blew past 220s and the client had to cancel). On timeout the
# tier is abandoned and its engines are reported with state "timeout".
# ponytail: a single module constant + one asyncio.wait_for; promote to config
# only if a caller ever needs to tune it per-request.
TIER3_TIMEOUT_S = 180.0

# Event callback: called (awaited) with a JSON-serializable dict as engines
# queue, start, and finish, so a UI can show live progress. Optional everywhere
# (defaults to None) — when None the execution path is byte-for-byte the legacy
# behavior, so the CLI and existing callers are unaffected.
EventCallback = Callable[[dict[str, Any]], Awaitable[None]]

# Engine name -> short tier label ("tier1" | "tier2" | "tier3"), for the upfront
# "queued" event so a UI can paint the full checklist before anything runs.
_ENGINE_TIER: dict[str, str] = {
    name: ("tier1" if tier == "tier1_fast" else "tier2" if tier == "tier2_medium" else "tier3")
    for tier, names in ENGINE_TIERS.items()
    for name in names
}


def tier_of(engine: str) -> str:
    """Return the short tier label for *engine* (defaults to tier1 if unknown)."""
    return _ENGINE_TIER.get(engine, "tier1")


def _engines_in_tier(tier_name: str, available: dict[str, Any]) -> list[str]:
    """Return engines that are both in the given tier and in available engines."""
    return [e for e in ENGINE_TIERS[tier_name] if e in available]


def _engine_event(name: str, tier: str | None, st: dict[str, Any]) -> dict[str, Any]:
    """Build a JSON-safe per-engine event from a status entry.

    Drops the heavy ``results`` payload (SearchResult objects) — the final
    response carries those; the live event only needs the metadata.
    """
    ev: dict[str, Any] = {"type": "engine", "engine": name, "tier": tier}
    ev.update({k: v for k, v in st.items() if k != "results"})
    return ev


async def _run_tier(
    engine_names: list[str],
    engines: dict[str, Callable[[], Any]],
    *,
    on_event: EventCallback | None = None,
    tier: str | None = None,
) -> tuple[list[SearchResult], dict[str, str], dict[str, dict[str, Any]]]:
    """Run a set of engines in parallel.

    Returns ``(results, answers, status)``:
      * ``results``  — flattened SearchResult items from every engine.
      * ``answers``  — engine name → synthesized answer text (only for engines
        that return a non-empty ``(answer, items)`` tuple; the answer was
        previously discarded here, which silently dropped Perplexity/Vane/etc.).
      * ``status``   — engine name → {state: ok|empty|failed, count, reason?, ms,
        results}.  ``results`` is the engine's raw (pre-fusion) item list, carried
        here so the dashboard can show "everything each source returned"; it is a
        list of SearchResult objects and is popped off by ``smart_search_rich``
        before ``status`` is serialized to JSON.

    When ``on_event`` is given it is awaited with a ``running`` event as each
    engine starts and a finished event the moment that engine completes — fired
    from inside each engine's gather coroutine, so events stream in completion
    order even though the gather only returns once the whole tier is done.
    """
    results: list[SearchResult] = []
    answers: dict[str, str] = {}
    status: dict[str, dict[str, Any]] = {}
    names = [n for n in engine_names if n in engines]
    if not names:
        return results, answers, status

    async def _run_one(name: str) -> tuple[str, list[SearchResult], str | None, dict[str, Any]]:
        if on_event is not None:
            await on_event({"type": "engine", "engine": name, "state": "running", "tier": tier})
        # timed_call wraps each engine so we capture per-engine latency (even for
        # failures/timeouts) for the dashboard timing panel; it never raises.
        res, exc, ms = await timed_call(engines[name]())
        items: list[SearchResult] = []
        answer: str | None = None
        if exc is not None:
            st: dict[str, Any] = {"state": "failed", "count": 0, "reason": failure_reason(exc), "ms": ms}
        elif isinstance(res, tuple):
            _msg, raw_items = res
            items = raw_items or []
            if _msg:
                answer = _msg
            st = {
                "state": "ok" if (items or _msg) else "empty", "count": len(items),
                "ms": ms, "results": items,
            }
            # An answer engine can be "ok" with 0 link results — it produced prose,
            # not links. Flag it so the panel shows "answer" instead of a bare 0.
            if _msg:
                st["answer"] = True
        elif isinstance(res, list):
            items = res
            st = {"state": "ok" if res else "empty", "count": len(res), "ms": ms, "results": res}
        else:
            st = {"state": "empty", "count": 0, "ms": ms, "results": []}
        if on_event is not None:
            await on_event(_engine_event(name, tier, st))
        return name, items, answer, st

    raw = await asyncio.gather(*(_run_one(n) for n in names))
    for name, items, answer, st in raw:
        results.extend(items)
        if answer:
            answers[name] = answer
        status[name] = st
    return results, answers, status


async def tiered_search(
    query: str,
    engines: dict[str, Callable[[], Any]],
    category: QueryCategory | None = None,
    max_results: int = 5,
    include_slow: bool = False,
    *,
    on_event: EventCallback | None = None,
) -> tuple[list[SearchResult], dict[str, str], dict[str, dict[str, Any]]]:
    """Execute engines in tiers, returning results, captured answers, and per-engine status.

    1. Run tier1 engines in parallel, collect results.
    2. Run tier2 when ``include_slow`` is set (cumulative ``deep``), when tier1
       was sparse (<3 results), or when the category routes to tier2 engines.
    3. Run tier3 when ``include_slow`` is set, the category is DEEP_RESEARCH, or
       the category routes to tier3 engines — bounded by ``TIER3_TIMEOUT_S``.
    4. Any built-but-not-run engine is reported with state "skipped".

    Returns ``(results, answers, status)``.  ``max_results`` is the PER-ENGINE
    cap (each engine is built with it upstream) — it is intentionally NOT used to
    truncate the aggregate here; capping the combined pool for display is the
    caller's responsibility.  ``on_event`` (optional) receives live tier/engine
    progress events; see ``_run_tier``.
    """
    all_results: list[SearchResult] = []
    all_answers: dict[str, str] = {}
    all_status: dict[str, dict[str, Any]] = {}

    async def _emit_tier(tier: str) -> None:
        if on_event is not None:
            await on_event({"type": "tier", "tier": tier, "state": "start"})

    # --- Tier 1 ---
    tier1_engines = _engines_in_tier("tier1_fast", engines)
    await _emit_tier("tier1")
    tier1_results, tier1_answers, tier1_status = await _run_tier(
        tier1_engines, engines, on_event=on_event, tier="tier1",
    )
    all_results.extend(tier1_results)
    all_answers.update(tier1_answers)
    all_status.update(tier1_status)

    # Track which engines have already run
    ran = set(tier1_engines)

    # --- Tier 2 ---
    tier2_engines = _engines_in_tier("tier2_medium", engines)
    # Determine extra engines routed by category that are in tier2/3
    category_extras_t2 = []
    if category is not None:
        from monster_search._router import get_engines_for_category
        routed = get_engines_for_category(category)
        category_extras_t2 = [
            e for e in routed
            if e in engines and e not in ran and e in ENGINE_TIERS["tier2_medium"]
        ]

    # Cumulative deep: ``include_slow`` now pulls in the tier2 AI engines
    # (Vane/Khoj/Fyin) too, not just tier3 — previously deep skipped tier2
    # unless tier1 came back nearly empty.
    run_tier2 = include_slow or len(tier1_results) < 3 or bool(category_extras_t2)
    if run_tier2:
        # Combine standard tier2 + category extras (dedup)
        t2_to_run = list(dict.fromkeys(tier2_engines + category_extras_t2))
        t2_to_run = [e for e in t2_to_run if e not in ran]
        if t2_to_run:
            await _emit_tier("tier2")
            tier2_results, tier2_answers, tier2_status = await _run_tier(
                t2_to_run, engines, on_event=on_event, tier="tier2",
            )
            all_results.extend(tier2_results)
            all_answers.update(tier2_answers)
            all_status.update(tier2_status)
            ran.update(t2_to_run)

    # --- Tier 3 ---
    tier3_engines = _engines_in_tier("tier3_slow", engines)
    category_extras_t3 = []
    if category is not None:
        from monster_search._router import get_engines_for_category
        routed = get_engines_for_category(category)
        category_extras_t3 = [
            e for e in routed
            if e in engines and e not in ran and e in ENGINE_TIERS["tier3_slow"]
        ]

    run_tier3 = include_slow or (category in _DEEP_CATEGORIES) or bool(category_extras_t3)
    if run_tier3:
        t3_to_run = list(dict.fromkeys(tier3_engines + category_extras_t3))
        t3_to_run = [e for e in t3_to_run if e not in ran]
        if t3_to_run:
            await _emit_tier("tier3")
            try:
                tier3_results, tier3_answers, tier3_status = await asyncio.wait_for(
                    _run_tier(t3_to_run, engines, on_event=on_event, tier="tier3"),
                    timeout=TIER3_TIMEOUT_S,
                )
                all_results.extend(tier3_results)
                all_answers.update(tier3_answers)
                all_status.update(tier3_status)
            except (asyncio.TimeoutError, TimeoutError):
                # Slow tier blew the cap — abandon it and report each engine that
                # didn't already finish as timed out (rather than blocking forever).
                for name in t3_to_run:
                    if name not in all_status:
                        st = {
                            "state": "timeout", "count": 0,
                            "reason": f"exceeded {int(TIER3_TIMEOUT_S)}s cap",
                            "ms": int(TIER3_TIMEOUT_S * 1000),
                        }
                        all_status[name] = st
                        if on_event is not None:
                            await on_event(_engine_event(name, "tier3", st))
            ran.update(t3_to_run)

    # Mark built-but-not-run engines as skipped (only reachable via deep mode).
    for name in engines:
        if name not in all_status:
            all_status[name] = {"state": "skipped", "count": 0, "reason": "deep mode only"}

    return all_results, all_answers, all_status
