from __future__ import annotations

import httpx
import pytest
import respx

from monster_search.clients.changedetection_client import ChangeDetectionClient
from monster_search.config import Config

BASE = "http://localhost:8086/api/v1"


def _config() -> Config:
    return Config(changedetection_api_key="test-api-key")


def test_changedetection_missing_api_key():
    config = Config(changedetection_api_key="")
    client = ChangeDetectionClient(config=config)
    with pytest.raises(ValueError, match="API key required"):
        client.add_watch("https://example.com")


@respx.mock
def test_add_watch():
    respx.post(f"{BASE}/watch").mock(
        return_value=httpx.Response(200, json={"uuid": "abc-123", "url": "https://example.com"})
    )
    client = ChangeDetectionClient(config=_config())
    result = client.add_watch("https://example.com", tag="test")
    assert result["uuid"] == "abc-123"
    request = respx.calls[0].request
    assert request.headers["x-api-key"] == "test-api-key"


@respx.mock
def test_list_watches():
    respx.get(f"{BASE}/watch").mock(
        return_value=httpx.Response(200, json={
            "uuid-1": {"url": "https://a.com", "tag": "news"},
            "uuid-2": {"url": "https://b.com", "tag": "tech"},
        })
    )
    client = ChangeDetectionClient(config=_config())
    watches = client.list_watches()
    assert len(watches) == 2
    assert watches[0]["uuid"] in ("uuid-1", "uuid-2")


@respx.mock
def test_list_watches_filter_by_tag():
    respx.get(f"{BASE}/watch").mock(
        return_value=httpx.Response(200, json={
            "uuid-1": {"url": "https://a.com", "tag": "news"},
            "uuid-2": {"url": "https://b.com", "tag": "tech"},
        })
    )
    client = ChangeDetectionClient(config=_config())
    watches = client.list_watches(tag="news")
    assert len(watches) == 1
    assert watches[0]["tag"] == "news"


@respx.mock
def test_get_latest():
    respx.get(f"{BASE}/watch/abc-123/history/latest").mock(
        return_value=httpx.Response(200, text="Page content here")
    )
    client = ChangeDetectionClient(config=_config())
    text = client.get_latest("abc-123")
    assert text == "Page content here"


@respx.mock
def test_get_diff():
    respx.get(f"{BASE}/watch/abc-123/diff/latest").mock(
        return_value=httpx.Response(200, text="+ Added line\n- Removed line")
    )
    client = ChangeDetectionClient(config=_config())
    text = client.get_diff("abc-123")
    assert "+ Added line" in text


@respx.mock
def test_remove_watch():
    respx.delete(f"{BASE}/watch/abc-123").mock(
        return_value=httpx.Response(204)
    )
    client = ChangeDetectionClient(config=_config())
    assert client.remove_watch("abc-123") is True
