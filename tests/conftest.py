"""Shared test fixtures for monster-search."""

from __future__ import annotations

import os

import pytest

from monster_search.clients._pool import close_all as _close_pool


@pytest.fixture(autouse=True)
def _clean_monster_env(request, monkeypatch):
    """Ensure unit tests use Config defaults, not values from .env or shell.

    Integration tests are exempt. They talk to real services and are configured
    through exactly these variables, so stripping them meant setting one in the
    shell did nothing at all and a .env file was the only way to configure a
    live run, with no error to say so.
    """
    if not request.node.get_closest_marker("integration"):
        for key in list(os.environ):
            if key.startswith("MONSTER_"):
                monkeypatch.delenv(key, raising=False)
    # Reset connection pool so respx mocking works (no stale clients)
    _close_pool()
