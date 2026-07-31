"""Connection pool for reusable httpx clients across search calls."""

from __future__ import annotations

import httpx

_sync_clients: dict[tuple[str, float], httpx.Client] = {}
_async_clients: dict[tuple[str, float], httpx.AsyncClient] = {}

_DEFAULT_HEADERS = {
    "User-Agent": "monster-search/0.6.0 (search aggregator; +https://github.com/scaso01/monster-search)",
}


def _origin_key(base_url: str, timeout: float) -> tuple[str, float]:
    """Build a cache key from the URL origin and timeout."""
    parsed = httpx.URL(base_url)
    host = parsed.host
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    origin = f"{parsed.scheme}://{host}:{port}"
    return (origin, timeout)


def get_client(base_url: str, timeout: float) -> httpx.Client:
    """Get or create a reusable sync httpx.Client for the given origin+timeout."""
    key = _origin_key(base_url, timeout)
    client = _sync_clients.get(key)
    if client is None:
        client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
        _sync_clients[key] = client
    return client


def get_async_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    """Get or create a reusable async httpx.AsyncClient for the given origin+timeout."""
    key = _origin_key(base_url, timeout)
    client = _async_clients.get(key)
    if client is None:
        client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=_DEFAULT_HEADERS,
        )
        _async_clients[key] = client
    return client


def close_all() -> None:
    """Close and discard all pooled clients. Call in test teardown."""
    for client in _sync_clients.values():
        try:
            client.close()
        except Exception:
            pass
    _sync_clients.clear()

    for client in _async_clients.values():
        try:
            # AsyncClient.close() is a coroutine but we need sync cleanup.
            # aclose() is also async. Just discard — Python GC handles sockets.
            pass
        except Exception:
            pass
    _async_clients.clear()
