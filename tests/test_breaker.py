"""Tests for the circuit breaker module."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from monster_search._breaker import (
    CircuitOpenError,
    EngineBreaker,
    get_breaker,
    reset_all,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset breaker registry between tests."""
    reset_all()
    yield
    reset_all()


# --- EngineBreaker state transitions ---


@pytest.mark.asyncio
async def test_closed_on_success():
    breaker = EngineBreaker("test", fail_max=2)
    result = await breaker.call(_succeed("ok"))
    assert result == "ok"
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_stays_closed_under_threshold():
    breaker = EngineBreaker("test", fail_max=3)
    # Two failures — still under fail_max
    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(_fail())
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_opens_at_fail_max():
    breaker = EngineBreaker("test", fail_max=2)
    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(_fail())
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_open_rejects_calls():
    breaker = EngineBreaker("test", fail_max=1, reset_timeout=9999)
    with pytest.raises(ValueError):
        await breaker.call(_fail())
    assert breaker.state == "open"

    with pytest.raises(CircuitOpenError) as exc_info:
        await breaker.call(_succeed("ignored"))
    assert exc_info.value.engine == "test"
    assert exc_info.value.retry_after > 0


@pytest.mark.asyncio
async def test_half_open_after_timeout():
    breaker = EngineBreaker("test", fail_max=1, reset_timeout=0.01)
    with pytest.raises(ValueError):
        await breaker.call(_fail())
    assert breaker.state == "open"

    # Wait for reset timeout
    await asyncio.sleep(0.02)
    assert breaker.state == "half_open"


@pytest.mark.asyncio
async def test_half_open_success_closes():
    breaker = EngineBreaker("test", fail_max=1, reset_timeout=0.01)
    with pytest.raises(ValueError):
        await breaker.call(_fail())

    await asyncio.sleep(0.02)
    result = await breaker.call(_succeed("recovered"))
    assert result == "recovered"
    assert breaker.state == "closed"


@pytest.mark.asyncio
async def test_half_open_failure_reopens():
    breaker = EngineBreaker("test", fail_max=1, reset_timeout=0.01)
    with pytest.raises(ValueError):
        await breaker.call(_fail())

    await asyncio.sleep(0.02)
    with pytest.raises(ValueError):
        await breaker.call(_fail())
    assert breaker.state == "open"


@pytest.mark.asyncio
async def test_success_resets_failure_count():
    breaker = EngineBreaker("test", fail_max=3)
    # Two failures, then a success
    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(_fail())
    await breaker.call(_succeed("ok"))
    assert breaker._failure_count == 0
    assert breaker.state == "closed"

    # Two more failures — should NOT open (count was reset)
    for _ in range(2):
        with pytest.raises(ValueError):
            await breaker.call(_fail())
    assert breaker.state == "closed"


# --- CircuitOpenError ---


def test_circuit_open_error_attrs():
    err = CircuitOpenError("searxng", 42.5)
    assert err.engine == "searxng"
    assert err.retry_after == 42.5
    assert "searxng" in str(err)


# --- Registry ---


def test_get_breaker_fast_engine():
    b = get_breaker("searxng")
    assert b.engine == "searxng"
    assert b.fail_max == 3
    assert b.reset_timeout == 60.0


def test_get_breaker_slow_engine():
    b = get_breaker("vane")
    assert b.engine == "vane"
    assert b.fail_max == 2
    assert b.reset_timeout == 300.0


def test_get_breaker_unknown_engine():
    b = get_breaker("unknown_engine")
    assert b.fail_max == 3
    assert b.reset_timeout == 120.0


def test_get_breaker_returns_same_instance():
    b1 = get_breaker("arxiv")
    b2 = get_breaker("arxiv")
    assert b1 is b2


def test_reset_all_clears():
    get_breaker("searxng")
    reset_all()
    # New instance after reset
    b = get_breaker("searxng")
    assert b._failure_count == 0


# --- Helpers ---


async def _succeed(value: str) -> str:
    return value


async def _fail() -> str:
    raise ValueError("boom")
