"""Shared test fixtures for monster-search."""

from __future__ import annotations

import os
import socket

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


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    """Unit tests must not reach the network.

    A CLI test that mocked SearXNG alone still ran every other engine for
    real, so on a CI runner a live YouTube result outranked the mocked one and
    the build failed at random. Anything unmocked now fails immediately and
    says so, instead of depending on what the internet feels like returning.
    """
    if request.node.get_closest_marker("integration"):
        return

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def _is_local(address: object) -> bool:
        """Loopback and non-IP sockets stay allowed.

        asyncio builds its own event loop out of a local socket pair, so
        blocking every connect breaks the test runner itself rather than
        catching anything. Only the public internet is off limits here.
        """
        if not isinstance(address, tuple) or not address:
            return True
        return str(address[0]) in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")

    def _check(address: object) -> None:
        if not _is_local(address):
            raise RuntimeError(
                f"a unit test tried to reach {address!r}: mock it, or mark the "
                "test with @pytest.mark.integration to run it against live services"
            )

    def connect(self, address, *args, **kwargs):
        _check(address)
        return real_connect(self, address, *args, **kwargs)

    def connect_ex(self, address, *args, **kwargs):
        _check(address)
        return real_connect_ex(self, address, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        _check(address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
