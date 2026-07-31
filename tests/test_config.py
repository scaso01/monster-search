from __future__ import annotations

from monster_search.config import Config


def test_config_defaults(monkeypatch):
    for var in ("MONSTER_SEARXNG_URL", "MONSTER_PERPLEXICA_URL", "MONSTER_DEFAULT_ENGINE",
                "MONSTER_MAX_RESULTS", "MONSTER_TIMEOUT", "MONSTER_PERPLEXICA_TIMEOUT",
                "MONSTER_PERPLEXICA_MODEL",
                "MONSTER_LOCAL_RESEARCHER_URL", "MONSTER_LOCAL_RESEARCHER_TIMEOUT",
                "MONSTER_MARGINALIA_URL", "MONSTER_MARGINALIA_TIMEOUT",
                "MONSTER_CRAWL4AI_URL", "MONSTER_CRAWL4AI_TIMEOUT",
                "MONSTER_PERPLEXITY_SESSION_TOKEN", "MONSTER_PERPLEXITY_TIMEOUT",
                "MONSTER_CHANGEDETECTION_URL", "MONSTER_CHANGEDETECTION_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    config = Config()
    assert config.searxng_url == "http://localhost:8080"
    assert config.perplexica_url == "http://localhost:3001"
    assert config.default_engine == "all"
    assert config.max_results == 5
    assert config.timeout == 15
    assert config.perplexica_timeout == 300
    assert config.perplexica_model == ""
    assert config.local_researcher_url == "http://localhost:8300"
    assert config.local_researcher_timeout == 600
    assert config.marginalia_url == "https://api.marginalia.nu"
    assert config.marginalia_timeout == 16
    assert config.crawl4ai_url == "http://localhost:11235"
    assert config.crawl4ai_timeout == 60
    assert config.perplexity_session_token == ""
    assert config.perplexity_timeout == 90
    assert config.changedetection_url == "http://localhost:8086"
    assert config.changedetection_api_key == ""


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MONSTER_SEARXNG_URL", "http://localhost:9090")
    monkeypatch.setenv("MONSTER_MAX_RESULTS", "10")
    config = Config()
    assert config.searxng_url == "http://localhost:9090"
    assert config.max_results == 10


def test_config_local_researcher_from_env(monkeypatch):
    monkeypatch.setenv("MONSTER_LOCAL_RESEARCHER_URL", "http://localhost:9300")
    monkeypatch.setenv("MONSTER_LOCAL_RESEARCHER_TIMEOUT", "900")
    config = Config()
    assert config.local_researcher_url == "http://localhost:9300"
    assert config.local_researcher_timeout == 900
