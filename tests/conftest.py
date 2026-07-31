"""Shared test fixtures for monster-search."""

from __future__ import annotations

import os

import pytest

from monster_search.clients._pool import close_all as _close_pool


@pytest.fixture(autouse=True)
def _clean_monster_env(monkeypatch):
    """Ensure tests use Config defaults, not values from .env or shell."""
    for key in list(os.environ):
        if key.startswith("MONSTER_"):
            monkeypatch.delenv(key, raising=False)
    # Reset connection pool so respx mocking works (no stale clients)
    _close_pool()
