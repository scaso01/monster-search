from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from unittest.mock import patch

import httpx
import respx

from monster_search.config import Config
from monster_search.health import check_health, _probe_perplexity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _atom_feed_with_entry() -> str:
    """Minimal Atom XML with one <entry> element."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<entry><title>Test</title></entry>'
        '</feed>'
    )


def _rss_with_item() -> str:
    """Minimal RSS XML with one <item> element."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        '<item><title>Test</title></item>'
        '</channel></rss>'
    )


def _mock_all_services(respx_mock: respx.MockRouter | None = None) -> None:
    """Mock all probe endpoints so tests don't make real network calls.

    Every mock here corresponds to the actual request issued by the matching
    _probe_* function in health.py.  When a probe hits an endpoint that isn't
    mocked, respx raises httpx.ConnectError which makes the probe return False
    — which then causes unrelated test assertions to fail.
    """
    # --- Docker / self-hosted ---

    # SearXNG: real query 'tokio rust', ≥1 result required
    respx.get("http://localhost:8080/search").mock(
        return_value=httpx.Response(200, json={"results": [{"url": "https://tokio.rs"}]})
    )

    # Archive.org Advanced Search. With no MONSTER_SSH_HOST configured — the
    # default, and what conftest's env purge guarantees here — the probe makes
    # this request directly rather than shelling out to SSH.
    respx.get(url__startswith="https://archive.org/advancedsearch.php").mock(
        return_value=httpx.Response(
            200, json={"response": {"docs": [{"identifier": "python-tutorial-2024"}]}}
        )
    )

    # Local Deep Researcher: heartbeat /ok
    respx.get("http://localhost:8300/ok").mock(
        return_value=httpx.Response(200, text="OK")
    )

    # Crawl4AI: heartbeat /health
    respx.get("http://localhost:11235/health").mock(
        return_value=httpx.Response(200, text="OK")
    )

    # Who-Dat: look up example.com, non-empty JSON
    respx.get("http://localhost:8083/example.com").mock(
        return_value=httpx.Response(200, json={"domain_name": "example.com"})
    )

    # Zoekt: POST /api/search, results under {"Result": {"Files": [...]}}
    respx.post("http://localhost:6070/api/search").mock(
        return_value=httpx.Response(200, json={"Result": {"Files": [{"FileName": "main.rs"}]}})
    )

    # Vane: provider list (non-empty body = UP)
    respx.get("http://localhost:3004/api/providers").mock(
        return_value=httpx.Response(200, json={"providers": ["brave"]})
    )

    # Khoj: heartbeat /api/health
    respx.get("http://localhost:42110/api/health").mock(
        return_value=httpx.Response(200, text="OK")
    )

    # Meilisearch: heartbeat /health
    respx.get("http://localhost:7700/health").mock(
        return_value=httpx.Response(200, json={"status": "available"})
    )

    # --- External APIs ---

    # arXiv: real query 'all:transformer', ≥1 Atom <entry>
    respx.get("https://export.arxiv.org/api/query").mock(
        return_value=httpx.Response(200, text=_atom_feed_with_entry())
    )

    # Semantic Scholar: real query 'transformer', ≥1 paper in data[]
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"data": [{"paperId": "1", "title": "Attention"}]})
    )

    # OpenAlex: real query 'transformer', ≥1 result
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "W1"}]})
    )

    # OSV: POST /v1/query for pypi:jinja2, ≥1 vuln
    respx.post("https://api.osv.dev/v1/query").mock(
        return_value=httpx.Response(200, json={"vulns": [{"id": "GHSA-test-0001"}]})
    )

    # deps.dev: npm:express, non-empty JSON
    respx.get("https://api.deps.dev/v3alpha/systems/npm/packages/express").mock(
        return_value=httpx.Response(200, json={"packageKey": {"system": "NPM", "name": "express"}})
    )

    # Google News RSS: rss/search endpoint, ≥1 <item>
    respx.get("https://news.google.com/rss/search").mock(
        return_value=httpx.Response(200, text=_rss_with_item())
    )

    # Marginalia: /public/search/{query} endpoint (NOT root URL /)
    respx.get("https://api.marginalia.nu/public/search/tokio").mock(
        return_value=httpx.Response(200, json={"results": [{"url": "https://tokio.rs"}]})
    )

    # Archive.org: probe now routes via Monster SSH (no direct HTTP call from Beast).
    # The SSH subprocess mock is handled separately in each test via mock_sp.

    # grep.app: /api/search endpoint, ≥1 hit in hits.hits
    respx.get("https://grep.app/api/search").mock(
        return_value=httpx.Response(200, json={"hits": {"hits": [{"repo": "test/repo"}]}})
    )

    # Hacker News (Algolia): query 'rust', ≥1 hit
    respx.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json={"hits": [{"objectID": "1"}]})
    )

    # HuggingFace Hub: query 'bert', limit 1, ≥1 model
    respx.get("https://huggingface.co/api/models").mock(
        return_value=httpx.Response(200, json=[{"modelId": "bert-base-uncased"}])
    )

    # Reddit: old.reddit.com RSS/Atom feed, ≥1 <entry>
    respx.get("https://old.reddit.com/search.rss").mock(
        return_value=httpx.Response(200, text=_atom_feed_with_entry())
    )

    # Slickdeals RSS: ≥1 <item>
    respx.get("https://slickdeals.net/newsearch.php").mock(
        return_value=httpx.Response(200, text=_rss_with_item())
    )

    # CheapShark deals: ≥1 deal
    respx.get("https://www.cheapshark.com/api/1.0/deals").mock(
        return_value=httpx.Response(200, json=[{"dealID": "abc123"}])
    )


# ---------------------------------------------------------------------------
# Subprocess mock helpers
# ---------------------------------------------------------------------------

_ARCHIVE_ORG_JSON = '{"response": {"docs": [{"identifier": "python-tutorial-2024"}]}}'
_GH_SEARCH_JSON = '[{"path":"main.rs"}]'

_FakeResult = type("FakeResult", (), {})


def _make_subprocess_result(returncode: int, stdout: str, stderr: str = "") -> object:
    r = _FakeResult()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _subprocess_side_effect(cmd, **_kwargs):
    """Return appropriate subprocess results based on the command.

    - SSH commands (archive_org probe): return Advanced Search JSON.
    - 'test -f' SSH commands (fyin probe): return exit 0.
    - gh CLI commands: return a JSON list with one item.
    """
    if isinstance(cmd, (list, tuple)) and len(cmd) > 0:
        exe = cmd[0]
        if exe == "ssh":
            # Distinguish fyin's 'test -f' probe from archive_org's curl probe
            cmd_str = " ".join(str(c) for c in cmd)
            if "test -f" in cmd_str:
                return _make_subprocess_result(0, "")
            # archive_org Advanced Search SSH probe
            return _make_subprocess_result(0, _ARCHIVE_ORG_JSON)
        if exe == "gh":
            return _make_subprocess_result(0, _GH_SEARCH_JSON)
    return _make_subprocess_result(0, _GH_SEARCH_JSON)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@respx.mock
def test_health_all_up():
    _mock_all_services()
    with patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.side_effect = _subprocess_side_effect
        mock_sp.TimeoutExpired = __import__("subprocess").TimeoutExpired
        mock_sp.OSError = OSError
        status = check_health()
    assert status["searxng"] is True
    assert status["local_researcher"] is True
    assert status["crawl4ai"] is True
    assert status["archive_org"] is True


@respx.mock
def test_health_searxng_down():
    _mock_all_services()
    # Override SearXNG to be down
    respx.get("http://localhost:8080/search").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.side_effect = _subprocess_side_effect
        mock_sp.TimeoutExpired = __import__("subprocess").TimeoutExpired
        mock_sp.OSError = OSError
        status = check_health()
    assert status["searxng"] is False
    assert status["local_researcher"] is True
    assert status["archive_org"] is True


@respx.mock
def test_health_crawl4ai_down():
    _mock_all_services()
    respx.get("http://localhost:11235/health").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.side_effect = _subprocess_side_effect
        mock_sp.TimeoutExpired = __import__("subprocess").TimeoutExpired
        mock_sp.OSError = OSError
        status = check_health()
    assert status["crawl4ai"] is False
    assert status["searxng"] is True
    assert status["archive_org"] is True


@respx.mock
def test_health_local_researcher_down():
    _mock_all_services()
    respx.get("http://localhost:8300/ok").mock(
        side_effect=httpx.ConnectError("refused")
    )
    with patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.side_effect = _subprocess_side_effect
        mock_sp.TimeoutExpired = __import__("subprocess").TimeoutExpired
        mock_sp.OSError = OSError
        status = check_health()
    assert status["local_researcher"] is False
    assert status["searxng"] is True
    assert status["archive_org"] is True


@respx.mock
def test_health_archive_org_ssh_fails(monkeypatch):
    """archive_org reports DOWN when the configured SSH host is unreachable."""
    # Only meaningful on the SSH route, which is opt-in.
    monkeypatch.setenv("MONSTER_SSH_HOST", "testhost.example")
    _mock_all_services()

    def _ssh_fail(cmd, **_kwargs):
        if isinstance(cmd, (list, tuple)) and cmd[0] == "ssh":
            cmd_str = " ".join(str(c) for c in cmd)
            if "test -f" not in cmd_str:
                # archive_org probe SSH failure — empty stdout, non-zero exit
                return _make_subprocess_result(
                    255, "", "ssh: connect to host localhost: Connection refused"
                )
        return _make_subprocess_result(0, _GH_SEARCH_JSON)

    with patch("monster_search.health.subprocess") as mock_sp:
        mock_sp.run.side_effect = _ssh_fail
        mock_sp.TimeoutExpired = __import__("subprocess").TimeoutExpired
        mock_sp.OSError = OSError
        status = check_health()
    assert status["archive_org"] is False
    assert status["searxng"] is True


# ---------------------------------------------------------------------------
# perplexity probe — accept the browser-cookie path, not just the static token
# ---------------------------------------------------------------------------


def test_probe_perplexity_down_when_no_auth_configured():
    """Neither static token nor browser-cookie path → DOWN."""
    with patch.dict(os.environ, {
        "MONSTER_PERPLEXITY_SESSION_TOKEN": "",
        "MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER": "",
    }, clear=False):
        ok, reason = _probe_perplexity(Config())
    assert ok is False
    assert "MONSTER_PERPLEXITY" in reason


def test_probe_perplexity_up_with_browser_cookie_path():
    """Regression: the self-healing browser-cookie path must read UP. Previously
    the probe checked only the static token and falsely reported DOWN while the
    engine actually worked via MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER."""
    with patch.dict(os.environ, {
        "MONSTER_PERPLEXITY_SESSION_TOKEN": "",
        "MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER": "firefox",
    }, clear=False):
        ok, reason = _probe_perplexity(Config())
    assert ok is True
    assert reason == ""


def test_probe_perplexity_up_with_static_token():
    """Static token alone still reads UP (back-compat)."""
    with patch.dict(os.environ, {
        "MONSTER_PERPLEXITY_SESSION_TOKEN": "sometoken",
        "MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER": "",
    }, clear=False):
        ok, reason = _probe_perplexity(Config())
    assert ok is True
