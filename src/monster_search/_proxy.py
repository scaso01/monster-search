"""Outbound exit selection for engines that rate-limit by IP.

A shared VPN exit is shared with strangers. OpenAlex meters per IP
("Insufficient budget... Resets at midnight UTC") and Reddit throttles per IP, so
the allowance is routinely spent before your own query arrives. Measured on one
host: both answered 429 on the shared exit and 200 through a dedicated SOCKS5
exit in the same minute. That is why those two engines route through
MONSTER_SOCKS_PROXIES when it is set.

Direct egress stays as the final attempt, so losing a proxy host degrades these
engines back to their old rate-limited behaviour rather than taking them
offline. With no proxies configured the direct request is the only attempt and
behaviour is unchanged.
"""

from __future__ import annotations

import httpx

from monster_search.config import Config


def _attempts(config: Config) -> tuple[str | None, ...]:
    """Proxy URLs to try in order, then ``None`` for a direct request."""
    return (*config.socks_proxies, None)


def _should_retry(resp: httpx.Response) -> bool:
    """429 is the per-IP limit this module exists to dodge — try the next exit.

    Other statuses are the engine's real answer and are returned as-is so the
    caller's own error handling still sees them.
    """
    return resp.status_code == 429


def get_with_failover(
    config: Config,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float,
) -> httpx.Response:
    """GET ``url`` through the first exit that is not rate-limited."""
    last_error: Exception | None = None
    last_resp: httpx.Response | None = None
    for proxy in _attempts(config):
        try:
            with httpx.Client(
                proxy=proxy, timeout=timeout, headers=headers, follow_redirects=True
            ) as client:
                resp = client.get(url, params=params)
        except httpx.HTTPError as exc:
            last_error = exc
            continue
        if not _should_retry(resp):
            return resp
        last_resp = resp
    if last_resp is not None:
        return last_resp
    assert last_error is not None
    raise last_error


async def aget_with_failover(
    config: Config,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float,
) -> httpx.Response:
    """Async form of :func:`get_with_failover`."""
    last_error: Exception | None = None
    last_resp: httpx.Response | None = None
    for proxy in _attempts(config):
        try:
            async with httpx.AsyncClient(
                proxy=proxy, timeout=timeout, headers=headers, follow_redirects=True
            ) as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            last_error = exc
            continue
        if not _should_retry(resp):
            return resp
        last_resp = resp
    if last_resp is not None:
        return last_resp
    assert last_error is not None
    raise last_error
