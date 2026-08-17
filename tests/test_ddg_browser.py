from __future__ import annotations

import json

import httpx
import pytest
import respx

from monster_search.clients.ddg_browser import DdgBrowserClient, is_ad, unwrap_url
from monster_search.config import Config
from monster_search.models import SearchResult

# Shape captured from a live Crawl4AI run 2026-08-17: extracted_content is a
# JSON *string*, hrefs are //duckduckgo.com/l/?uddg= redirects, and paid rows
# sit in the same div.result container as organic ones.
ORGANIC_HREF = (
    "//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.rtings.com%2Fgaming-pc"
    "&rut=3c0dd6571081b9d9317fffd1f4616ab5"
)
AD_HREF = (
    "//duckduckgo.com/l/?uddg=https%3A%2F%2Fduckduckgo.com%2Fy.js%3Fad_domain"
    "%3Dchicagotribune.com%26ad_provider%3Dbingv7aa"
)

MOCK_RESPONSE = {
    "results": [
        {
            "success": True,
            "extracted_content": json.dumps(
                [
                    {"title": "Ad title", "url": AD_HREF, "snippet": "sponsored"},
                    {
                        "title": "Best Gaming PCs",
                        "url": ORGANIC_HREF,
                        "snippet": "Our picks for 2026.",
                    },
                    {
                        "title": "Budget Builds",
                        "url": "https://example.com/builds",
                        "snippet": "Under $800.",
                    },
                ]
            ),
        }
    ]
}


def _client() -> DdgBrowserClient:
    return DdgBrowserClient(config=Config())


@respx.mock
def test_search_returns_results_and_drops_ads() -> None:
    cfg = Config()
    respx.post(f"{cfg.crawl4ai_url}/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    results = _client().search("gaming pc reviews")

    assert [r.title for r in results] == ["Best Gaming PCs", "Budget Builds"]
    assert all(isinstance(r, SearchResult) for r in results)
    assert results[0].url == "https://www.rtings.com/gaming-pc"
    assert results[0].source == "ddg"


@respx.mock
def test_max_results_is_honoured() -> None:
    cfg = Config()
    respx.post(f"{cfg.crawl4ai_url}/crawl").mock(
        return_value=httpx.Response(200, json=MOCK_RESPONSE)
    )

    assert len(_client().search("q", max_results=1)) == 1


@respx.mock
def test_failed_crawl_returns_empty() -> None:
    cfg = Config()
    respx.post(f"{cfg.crawl4ai_url}/crawl").mock(
        return_value=httpx.Response(200, json={"results": [{"success": False}]})
    )

    assert _client().search("q") == []


@respx.mock
def test_layout_change_returns_empty_not_garbage() -> None:
    """Extraction returning prose (schema no longer matches) must not leak."""
    cfg = Config()
    respx.post(f"{cfg.crawl4ai_url}/crawl").mock(
        return_value=httpx.Response(
            200, json={"results": [{"success": True, "extracted_content": "no matches"}]}
        )
    )

    assert _client().search("q") == []


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        (ORGANIC_HREF, "https://www.rtings.com/gaming-pc"),
        ("https://plain.example.com/x", "https://plain.example.com/x"),
        ("", ""),
    ],
)
def test_unwrap_url(href: str, expected: str) -> None:
    assert unwrap_url(href) == expected


def test_is_ad() -> None:
    assert is_ad(AD_HREF)
    assert not is_ad(ORGANIC_HREF)


@pytest.mark.integration
def test_live_ddg_returns_results() -> None:
    """Smoke test against the real Crawl4AI service.

    This is the one that catches a DuckDuckGo layout change or a fresh block --
    the mocked tests above cannot.
    """
    results = _client().search("tokio rust", max_results=5)
    assert results, "live DuckDuckGo search returned nothing"
    assert all(r.url.startswith("http") for r in results)
