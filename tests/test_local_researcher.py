from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.local_researcher import LocalResearcherClient, _extract_sources
from monster_search.config import Config
from monster_search.models import SearchResult

BASE = "http://localhost:8300"
THREAD_ID = "test-thread-123"
ASSISTANT_ID = "test-assistant-456"

MOCK_REPORT = (
    "# Research Report\n\n"
    "Found info at [Python](https://python.org) and [Docs](https://docs.python.org).\n"
    "Also see [Real Python](https://realpython.com/async-io-python/)."
)


@respx.mock
def test_local_researcher_search_returns_report_and_sources():
    respx.post(f"{BASE}/threads").mock(
        return_value=httpx.Response(200, json={"thread_id": THREAD_ID})
    )
    respx.post(f"{BASE}/assistants/search").mock(
        return_value=httpx.Response(200, json=[{"assistant_id": ASSISTANT_ID}])
    )
    respx.post(f"{BASE}/threads/{THREAD_ID}/runs/wait").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    respx.get(f"{BASE}/threads/{THREAD_ID}/state").mock(
        return_value=httpx.Response(200, json={"values": {"running_summary": MOCK_REPORT}})
    )

    client = LocalResearcherClient()
    report, results = client.search("python research")

    assert "Research Report" in report
    assert len(results) == 3
    assert all(isinstance(r, SearchResult) for r in results)
    assert all(r.source == "local_researcher" for r in results)
    urls = [r.url for r in results]
    assert "https://python.org" in urls
    assert "https://docs.python.org" in urls
    assert "https://realpython.com/async-io-python/" in urls


@respx.mock
def test_local_researcher_empty_report():
    respx.post(f"{BASE}/threads").mock(
        return_value=httpx.Response(200, json={"thread_id": THREAD_ID})
    )
    respx.post(f"{BASE}/assistants/search").mock(
        return_value=httpx.Response(200, json=[{"assistant_id": ASSISTANT_ID}])
    )
    respx.post(f"{BASE}/threads/{THREAD_ID}/runs/wait").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    respx.get(f"{BASE}/threads/{THREAD_ID}/state").mock(
        return_value=httpx.Response(200, json={"values": {"running_summary": ""}})
    )

    client = LocalResearcherClient()
    report, results = client.search("empty query")

    assert report == ""
    assert results == []


@respx.mock
def test_local_researcher_no_assistant_raises():
    respx.post(f"{BASE}/threads").mock(
        return_value=httpx.Response(200, json={"thread_id": THREAD_ID})
    )
    respx.post(f"{BASE}/assistants/search").mock(
        return_value=httpx.Response(200, json=[])
    )

    client = LocalResearcherClient()
    with pytest.raises(RuntimeError, match="No assistant found"):
        client.search("test")


def test_extract_sources_deduplicates():
    md = "[A](https://a.com) and [B](https://b.com) then [A again](https://a.com)"
    results = _extract_sources(md)
    assert len(results) == 2
    urls = {r.url for r in results}
    assert urls == {"https://a.com", "https://b.com"}
    assert all(r.source == "local_researcher" for r in results)


def test_extract_sources_bullet_point_format():
    """LDR often produces bullet-point links instead of markdown links."""
    report = (
        "## Summary\n\n"
        "Found some info about Python.\n\n"
        "### Sources\n"
        "* Python Docs : https://docs.python.org\n"
        "* Real Python | Async IO : https://realpython.com/async-io-python/\n"
        "* PEP 492 : https://peps.python.org/pep-0492/\n"
    )
    results = _extract_sources(report)
    assert len(results) == 3
    urls = {r.url for r in results}
    assert "https://docs.python.org" in urls
    assert "https://realpython.com/async-io-python/" in urls
    assert "https://peps.python.org/pep-0492/" in urls
    assert results[0].title == "Python Docs"


def test_extract_sources_from_sources_gathered():
    """sources_gathered field from LangGraph state provides structured sources."""
    report = "A summary without any links."
    sources_gathered = [
        "* HP Store | 128GB RAM : https://www.hp.com/desktops\n* Tom's Hardware : https://www.tomshardware.com/reviews",
        "* Newegg 128GB : https://www.newegg.com/128gb\n* HP Store | 128GB RAM : https://www.hp.com/desktops",
    ]
    results = _extract_sources(report, sources_gathered)
    assert len(results) == 3  # 4 entries but HP is duplicated
    urls = {r.url for r in results}
    assert "https://www.hp.com/desktops" in urls
    assert "https://www.tomshardware.com/reviews" in urls
    assert "https://www.newegg.com/128gb" in urls


def test_extract_sources_prefers_sources_gathered_over_report():
    """sources_gathered entries come first in results ordering."""
    report = "See [Report Link](https://report.example.com) for details."
    sources_gathered = [
        "* Gathered Link : https://gathered.example.com",
    ]
    results = _extract_sources(report, sources_gathered)
    assert len(results) == 2
    # sources_gathered entries should appear first
    assert results[0].url == "https://gathered.example.com"
    assert results[1].url == "https://report.example.com"


@respx.mock
def test_local_researcher_with_sources_gathered():
    """Integration test: client extracts from sources_gathered in state."""
    respx.post(f"{BASE}/threads").mock(
        return_value=httpx.Response(200, json={"thread_id": THREAD_ID})
    )
    respx.post(f"{BASE}/assistants/search").mock(
        return_value=httpx.Response(200, json=[{"assistant_id": ASSISTANT_ID}])
    )
    respx.post(f"{BASE}/threads/{THREAD_ID}/runs/wait").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    respx.get(f"{BASE}/threads/{THREAD_ID}/state").mock(
        return_value=httpx.Response(200, json={
            "values": {
                "running_summary": "Summary with no markdown links.",
                "sources_gathered": [
                    "* HP Desktops : https://hp.com/desktops\n"
                    "* Tom's Hardware : https://tomshardware.com/ram",
                ],
            }
        })
    )

    client = LocalResearcherClient()
    report, results = client.search("128gb ram pc")

    assert "Summary" in report
    assert len(results) == 2
    urls = {r.url for r in results}
    assert "https://hp.com/desktops" in urls
    assert "https://tomshardware.com/ram" in urls


@respx.mock
@pytest.mark.asyncio
async def test_local_researcher_async_search():
    respx.post(f"{BASE}/threads").mock(
        return_value=httpx.Response(200, json={"thread_id": THREAD_ID})
    )
    respx.post(f"{BASE}/assistants/search").mock(
        return_value=httpx.Response(200, json=[{"assistant_id": ASSISTANT_ID}])
    )
    respx.post(f"{BASE}/threads/{THREAD_ID}/runs/wait").mock(
        return_value=httpx.Response(200, json={"status": "success"})
    )
    respx.get(f"{BASE}/threads/{THREAD_ID}/state").mock(
        return_value=httpx.Response(200, json={"values": {"running_summary": MOCK_REPORT}})
    )

    client = LocalResearcherClient()
    report, results = await client.asearch("python research")

    assert "Research Report" in report
    assert len(results) == 3
    assert all(r.source == "local_researcher" for r in results)
