"""Async circuit breaker for search engines."""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, Coroutine


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is open and calls are rejected."""

    def __init__(self, engine: str, retry_after: float) -> None:
        self.engine = engine
        self.retry_after = retry_after
        super().__init__(f"Circuit open for {engine}, retry in {retry_after:.0f}s")


def failure_reason(exc: BaseException) -> str:
    """Short, human-readable reason for an engine failure (for status panels)."""
    if isinstance(exc, CircuitOpenError):
        return "circuit-open"
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in type(exc).__name__:
        return "timeout"
    return f"{type(exc).__name__}: {exc}"[:120]


async def timed_call(coro: Coroutine[Any, Any, Any]) -> tuple[Any, BaseException | None, int]:
    """Await *coro*, returning ``(result, exception, elapsed_ms)``.

    Never raises — a failing engine returns ``(None, exc, ms)`` so callers can
    record both the failure and how long it took (e.g. a 30s timeout) for the
    dashboard's per-engine timing panel.
    """
    start = time.monotonic()
    try:
        result = await coro
        return result, None, int((time.monotonic() - start) * 1000)
    except BaseException as exc:  # noqa: BLE001 — re-surfaced via the returned tuple
        return None, exc, int((time.monotonic() - start) * 1000)


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class EngineBreaker:
    """Per-engine circuit breaker with CLOSED -> OPEN -> HALF_OPEN -> CLOSED flow."""

    def __init__(self, engine: str, fail_max: int = 3, reset_timeout: float = 120.0) -> None:
        self.engine = engine
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._state = _State.CLOSED
        self._failure_count = 0
        self._last_failure: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> str:
        """Return current state as a string, accounting for timeout expiry."""
        if self._state == _State.OPEN:
            if time.monotonic() - self._last_failure >= self.reset_timeout:
                return "half_open"
        return self._state.value

    async def call(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Wrap an async call with circuit breaker logic."""
        async with self._lock:
            now = time.monotonic()
            if self._state == _State.OPEN:
                if now - self._last_failure >= self.reset_timeout:
                    self._state = _State.HALF_OPEN
                else:
                    # Must close the unawaited coroutine to avoid RuntimeWarning
                    coro.close()
                    raise CircuitOpenError(
                        self.engine,
                        self.reset_timeout - (now - self._last_failure),
                    )

        try:
            result = await coro
        except Exception:
            async with self._lock:
                self._failure_count += 1
                self._last_failure = time.monotonic()
                if self._failure_count >= self.fail_max:
                    self._state = _State.OPEN
            raise

        # Success — reset
        async with self._lock:
            self._failure_count = 0
            self._state = _State.CLOSED
        return result


# --- Registry ---

_FAST_ENGINES = frozenset({
    "searxng", "ddg", "marginalia", "mwmbl", "news", "semantic_scholar", "arxiv",
    "openalex", "osv", "deps", "gnews", "whodat", "zoekt", "archive_org",
    "youtube", "grepapp", "github_code",
    "hackernews", "huggingface", "reddit", "github_repos",
    "priceghost",
})
_SLOW_ENGINES = frozenset({
    "local_researcher", "perplexity", "crawl4ai",
    "vane", "khoj", "fyin", "synthesizer",
    "amazon_deals", "newegg",
})

_breakers: dict[str, EngineBreaker] = {}


def get_breaker(engine_name: str) -> EngineBreaker:
    """Lazy-create and return a circuit breaker for *engine_name*."""
    if engine_name not in _breakers:
        if engine_name in _SLOW_ENGINES:
            _breakers[engine_name] = EngineBreaker(engine_name, fail_max=2, reset_timeout=300.0)
        elif engine_name in _FAST_ENGINES:
            _breakers[engine_name] = EngineBreaker(engine_name, fail_max=3, reset_timeout=60.0)
        else:
            _breakers[engine_name] = EngineBreaker(engine_name)
    return _breakers[engine_name]


def reset_all() -> None:
    """Clear registry (useful in tests)."""
    _breakers.clear()
