"""Exit failover for the two engines that rate-limit per IP."""

from __future__ import annotations

import httpx
import pytest
import respx

from monster_search._proxy import aget_with_failover, get_with_failover
from monster_search.config import Config

_URL = "https://api.example.com/works"


def _config(*proxies: str) -> Config:
    return Config(socks_proxies=tuple(proxies))


@respx.mock
def test_falls_through_to_next_exit_on_429():
    """A 429 is the shared-exit budget being exhausted by strangers, not the
    engine's answer — the next exit gets a turn."""
    route = respx.get(_URL)
    route.side_effect = [
        httpx.Response(429, json={"error": "Rate limit exceeded"}),
        httpx.Response(200, json={"results": [{"id": "W1"}]}),
    ]
    resp = get_with_failover(_config("socks5://10.0.0.2:1085"), _URL, timeout=5)
    assert resp.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_direct_egress_is_the_last_attempt():
    """Losing every Oracle host degrades these engines to their old
    rate-limited behaviour rather than taking them offline."""
    route = respx.get(_URL)
    route.side_effect = [
        httpx.ConnectError("oracle down"),
        httpx.ConnectError("oracle down"),
        httpx.Response(200, json={"results": []}),
    ]
    cfg = _config("socks5://10.0.0.2:1085", "socks5://10.0.0.2:1086")
    resp = get_with_failover(cfg, _URL, timeout=5)
    assert resp.status_code == 200
    assert route.call_count == 3


@respx.mock
def test_non_429_error_is_returned_not_retried():
    """A 500 is the engine's real answer. Retrying it on every exit would
    triple the load on something already struggling."""
    route = respx.get(_URL).mock(return_value=httpx.Response(500))
    resp = get_with_failover(_config("socks5://10.0.0.2:1085"), _URL, timeout=5)
    assert resp.status_code == 500
    assert route.call_count == 1


@respx.mock
def test_returns_last_429_when_every_exit_is_limited():
    """Every exit limited is a genuine failure — surface the 429 so the caller's
    raise_for_status still fires instead of silently returning nothing."""
    route = respx.get(_URL).mock(return_value=httpx.Response(429))
    cfg = _config("socks5://10.0.0.2:1085", "socks5://10.0.0.2:1086")
    resp = get_with_failover(cfg, _URL, timeout=5)
    assert resp.status_code == 429
    assert route.call_count == 3


@respx.mock
def test_raises_when_every_exit_errors():
    route = respx.get(_URL)
    route.side_effect = httpx.ConnectError("no route")
    with pytest.raises(httpx.ConnectError):
        get_with_failover(_config("socks5://10.0.0.2:1085"), _URL, timeout=5)


@respx.mock
@pytest.mark.asyncio
async def test_async_falls_through_to_next_exit_on_429():
    route = respx.get(_URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json={"results": [{"id": "W1"}]}),
    ]
    cfg = _config("socks5://10.0.0.2:1085")
    resp = await aget_with_failover(cfg, _URL, timeout=5)
    assert resp.status_code == 200
    assert route.call_count == 2
