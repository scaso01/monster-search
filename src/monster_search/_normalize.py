"""URL normalization for deduplicating search results."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Tracking parameters to strip from URLs
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "medium", "campaign",
})


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Lowercase hostname
    - Remove www. prefix
    - Prefer https
    - Remove fragments
    - Strip tracking params (utm_*, fbclid, gclid, ref, source, medium, campaign)
    - Sort remaining query params
    - Remove trailing slashes from path
    """
    parsed = urlparse(url)

    # Lowercase hostname and remove www. prefix
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    # Prefer https
    scheme = "https"

    # Strip fragment
    fragment = ""

    # Filter and sort query params
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {
        k: v for k, v in params.items()
        if k.lower() not in _TRACKING_PARAMS
    }
    # Sort params; each value list is kept as-is
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)

    # Remove trailing slashes from path (but keep "/" for root)
    path = parsed.path.rstrip("/") or "/"

    # Reconstruct with port if non-standard
    port = parsed.port
    if port and port not in (80, 443):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, fragment))


def urls_match(url1: str, url2: str) -> bool:
    """Check if two URLs are equivalent after normalization."""
    return normalize_url(url1) == normalize_url(url2)
