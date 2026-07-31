"""Configuration loaded from environment variables with sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env at import time so Config() always sees MONSTER_* vars,
# whether invoked via CLI or as a library.
if not load_dotenv():
    _pkg_env = Path(__file__).resolve().parents[2] / ".env"
    if _pkg_env.is_file():
        load_dotenv(_pkg_env)


@dataclass(frozen=True, slots=True)
class Config:
    """Monster search configuration."""

    searxng_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_SEARXNG_URL", "http://localhost:8080")
    )
    perplexica_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PERPLEXICA_URL", "http://localhost:3001")
    )
    default_engine: str = field(
        default_factory=lambda: os.environ.get("MONSTER_DEFAULT_ENGINE", "all")
    )
    max_results: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_MAX_RESULTS", "5"))
    )
    timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_TIMEOUT", "15"))
    )
    perplexica_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_PERPLEXICA_TIMEOUT", "300"))
    )
    perplexica_model: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PERPLEXICA_MODEL", "")
    )
    local_researcher_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_LOCAL_RESEARCHER_URL", "http://localhost:8300")
    )
    local_researcher_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_LOCAL_RESEARCHER_TIMEOUT", "600"))
    )

    # Marginalia (replaced Stract — dead since July 2025)
    marginalia_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_MARGINALIA_URL", "https://api.marginalia.nu")
    )
    marginalia_timeout: int = field(
        # 10s was just under marginalia's typical ~11s response → consistent
        # ReadTimeout, dropping it from every smart search. 16s lets it land
        # (still well under the synthesizer's ~19s, so no added wall-clock).
        default_factory=lambda: int(os.environ.get("MONSTER_MARGINALIA_TIMEOUT", "16"))
    )

    # Mwmbl (free, keyless independent open-source web index — pairs with marginalia)
    mwmbl_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_MWMBL_URL", "https://api.mwmbl.org")
    )
    mwmbl_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_MWMBL_TIMEOUT", "10"))
    )

    # Crawl4AI
    crawl4ai_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_CRAWL4AI_URL", "http://localhost:11235")
    )
    crawl4ai_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_CRAWL4AI_TIMEOUT", "60"))
    )

    # Perplexity
    # Preferred: read the live session cookie straight from a local browser
    # profile (e.g. "firefox" or "firefox:default-release"), so the token
    # self-heals on every login — no long-lived secret stored on disk.
    # Falls back to the static token below when unset or extraction fails.
    perplexity_cookies_from_browser: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PERPLEXITY_COOKIES_FROM_BROWSER", "")
    )
    perplexity_session_token: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PERPLEXITY_SESSION_TOKEN", "")
    )
    perplexity_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_PERPLEXITY_TIMEOUT", "90"))
    )

    # changedetection.io
    changedetection_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_CHANGEDETECTION_URL", "http://localhost:8086")
    )
    changedetection_api_key: str = field(
        default_factory=lambda: os.environ.get("MONSTER_CHANGEDETECTION_API_KEY", "")
    )

    # Archive.org
    archive_org_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_ARCHIVE_ORG_TIMEOUT", "60"))
    )

    # Academic — Semantic Scholar
    semantic_scholar_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_SEMANTIC_SCHOLAR_TIMEOUT", "15"))
    )
    semantic_scholar_api_key: str = field(
        default_factory=lambda: os.environ.get("MONSTER_SEMANTIC_SCHOLAR_API_KEY", "")
    )

    # Academic — arXiv
    arxiv_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_ARXIV_TIMEOUT", "15"))
    )

    # Academic — OpenAlex
    openalex_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_OPENALEX_TIMEOUT", "15"))
    )
    openalex_mailto: str = field(
        default_factory=lambda: os.environ.get("MONSTER_OPENALEX_MAILTO", "")
    )

    # Security — OSV.dev
    osv_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_OSV_TIMEOUT", "10"))
    )

    # Packages — deps.dev
    deps_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_DEPS_TIMEOUT", "10"))
    )

    # News — Google News RSS
    gnews_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_GNEWS_TIMEOUT", "10"))
    )

    # WHOIS — Who-Dat (self-hosted)
    whodat_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_WHODAT_URL", "http://localhost:8083")
    )
    whodat_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_WHODAT_TIMEOUT", "10"))
    )

    # Code Search — Zoekt (self-hosted)
    zoekt_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_ZOEKT_URL", "http://localhost:6070")
    )
    zoekt_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_ZOEKT_TIMEOUT", "10"))
    )

    # Vane AI search (Perplexica fork, self-hosted)
    vane_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_VANE_URL", "http://localhost:3004")
    )
    vane_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_VANE_TIMEOUT", "300"))
    )

    # Khoj AI search (self-hosted, anonymous mode)
    khoj_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_KHOJ_URL", "http://localhost:42110")
    )
    khoj_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_KHOJ_TIMEOUT", "300"))
    )

    # Remote host for the engines that shell out over SSH rather than HTTP
    # (fyin, and archive.org when the local exit IP is blocked). Format is
    # anything ssh accepts, e.g. "user@host". Empty means those engines stay
    # switched off — this must never default to a real machine, or a fresh
    # install tries to open SSH connections to somebody else's box.
    ssh_host: str = field(
        default_factory=lambda: os.environ.get("MONSTER_SSH_HOST", "")
    )

    # Fyin search (CLI on the SSH host)
    fyin_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_FYIN_TIMEOUT", "300"))
    )
    # Optional shell file sourced on the remote host before fyin runs, for the
    # API keys it expects in its environment. Empty means don't source anything.
    fyin_env_file: str = field(
        default_factory=lambda: os.environ.get("MONSTER_FYIN_ENV_FILE", "")
    )

    # Meilisearch (result cache)
    meilisearch_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_MEILISEARCH_URL", "http://localhost:7700")
    )
    meilisearch_key: str = field(
        default_factory=lambda: os.environ.get("MONSTER_MEILISEARCH_KEY", "")
    )
    meilisearch_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_MEILISEARCH_TIMEOUT", "10"))
    )

    # AI Synthesizer (any OpenAI-compatible endpoint, e.g. llama-server)
    llama_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_LLAMA_URL", "http://localhost:8080")
    )
    synthesizer_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_SYNTHESIZER_TIMEOUT", "120"))
    )

    # YouTube (yt-dlp + transcript)
    youtube_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_YOUTUBE_TIMEOUT", "30"))
    )
    youtube_max_transcript_chars: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_YOUTUBE_MAX_TRANSCRIPT_CHARS", "2000"))
    )

    # grep.app code search
    # grepapp_enabled defaults False: grep.app rate-limits whole hosting and
    # VPN address ranges, returning HTTP 429 in under a second, so it fails
    # instantly from many networks. Set MONSTER_GREPAPP_ENABLED=true to try it.
    grepapp_enabled: bool = field(
        default_factory=lambda: os.environ.get("MONSTER_GREPAPP_ENABLED", "").lower()
        in ("1", "true", "yes")
    )
    grepapp_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_GREPAPP_TIMEOUT", "15"))
    )

    # searchcode.com per-repo code search
    searchcode_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_SEARCHCODE_TIMEOUT", "15"))
    )

    # GitHub Code Search (gh CLI)
    github_code_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_GITHUB_CODE_TIMEOUT", "15"))
    )

    # GitHub Repos Discovery (gh CLI)
    github_repos_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_GITHUB_REPOS_TIMEOUT", "15"))
    )

    # Hacker News (Algolia API)
    hackernews_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_HACKERNEWS_TIMEOUT", "10"))
    )

    # HuggingFace Hub
    huggingface_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_HUGGINGFACE_TIMEOUT", "15"))
    )

    # Reddit
    reddit_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_REDDIT_TIMEOUT", "15"))
    )

    # Shopping — CheapShark (no key needed)
    cheapshark_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_CHEAPSHARK_TIMEOUT", "10"))
    )

    # Shopping — Slickdeals RSS
    slickdeals_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_SLICKDEALS_TIMEOUT", "15"))
    )

    # Shopping — Deal RSS feeds (r/buildapcsales, DealNews, etc.)
    deals_rss_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_DEALS_RSS_TIMEOUT", "15"))
    )

    # Shopping — PriceGhost (self-hosted price tracker)
    priceghost_url: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PRICEGHOST_URL", "http://localhost:3100")
    )
    priceghost_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_PRICEGHOST_TIMEOUT", "15"))
    )
    # The login endpoint takes an email. This was called priceghost_username
    # while every caller and test read priceghost_email, so the credential was
    # silently always empty; MONSTER_PRICEGHOST_USERNAME still works as a
    # fallback for anyone who already set it.
    priceghost_email: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PRICEGHOST_EMAIL")
        or os.environ.get("MONSTER_PRICEGHOST_USERNAME", "")
    )
    priceghost_password: str = field(
        default_factory=lambda: os.environ.get("MONSTER_PRICEGHOST_PASSWORD", "")
    )

    # Shopping — Amazon Deals (scraper)
    amazon_deals_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_AMAZON_DEALS_TIMEOUT", "30"))
    )

    # Shopping — Newegg (scraper)
    newegg_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MONSTER_NEWEGG_TIMEOUT", "30"))
    )
