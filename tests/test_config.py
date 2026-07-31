from __future__ import annotations

import re
from pathlib import Path

import pytest

from monster_search.config import Config

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"
_CONFIG_SRC = _REPO_ROOT / "src" / "monster_search" / "config.py"


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


# --- .env.example is documentation, so it is checked like documentation ------


def _config_defaults() -> dict[str, str]:
    """Every MONSTER_* default written as a plain literal in config.py."""
    src = _CONFIG_SRC.read_text(encoding="utf-8")
    return dict(
        re.findall(r'os\.environ\.get\("(MONSTER_[A-Z0-9_]+)",\s*"([^"]*)"\)', src)
    )


def _env_example_values() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(MONSTER_[A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


@pytest.mark.skipif(
    not _ENV_EXAMPLE.is_file(), reason="running against an installed copy, not the repo"
)
def test_env_example_matches_the_real_defaults():
    """.env.example says "defaults are shown below", so they must be the defaults.

    Four of them had drifted, which is the kind of thing nobody notices until
    someone copies the file and wonders why their timeouts differ.
    """
    real = _config_defaults()
    wrong = {
        var: (val, real[var])
        for var, val in _env_example_values().items()
        if val and var in real and val != real[var]
    }

    assert wrong == {}, "\n".join(
        f"{v}: .env.example says {a!r}, config.py defaults to {b!r}"
        for v, (a, b) in wrong.items()
    )


@pytest.mark.skipif(
    not _ENV_EXAMPLE.is_file(), reason="running against an installed copy, not the repo"
)
def test_env_example_documents_every_service_url():
    """A URL nobody can discover is a service nobody can point anywhere.

    Six self-hosted engines were missing from this file, so their only
    documented address was localhost and there was no hint you could change it.
    """
    documented = set(_env_example_values())
    url_vars = {v for v in _config_defaults() if v.endswith("_URL")}

    assert url_vars - documented == set()
