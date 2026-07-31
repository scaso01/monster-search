"""Integration tests -- require Docker containers to be running.

Run with: pytest tests/test_integration.py -v -m integration
Skip with: pytest tests/ -v -m "not integration"
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

from monster_search.clients.perplexica import PerplexicaClient
from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.health import check_health

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _require_monster():
    """Skip all tests in this module if Docker containers are down."""
    # Reload .env since conftest strips MONSTER_* vars for unit test isolation
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(env_path, override=True)
    status = check_health()
    if not status.get("searxng"):
        pytest.skip("SearXNG not reachable")


def test_searxng_live_search():
    client = SearXNGClient()
    results = client.search("python programming", max_results=3)
    assert len(results) > 0
    assert all(r.url for r in results)
    assert all(r.title for r in results)


def test_searxng_news_category():
    client = SearXNGClient()
    results = client.search("technology", category="news", max_results=3)
    assert len(results) > 0


def test_perplexica_live_search():
    status = check_health()
    if not status.get("perplexica"):
        pytest.skip("Perplexica not reachable")
    config = Config(timeout=120)  # llama-server inference can be slow
    client = PerplexicaClient(config=config)
    try:
        message, results = client.search("what is Python asyncio")
    except (httpx.HTTPStatusError, httpx.ReadTimeout) as exc:
        pytest.skip(f"Perplexica unavailable: {exc}")
    assert len(message) > 0
