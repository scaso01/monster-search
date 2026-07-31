# monster-search

Unified search hub -- 34 engines across 12 categories, smart tiered by default. Web, academic, code, security, packages, WHOIS, news, video, AI/ML, community, archive, and shopping search behind a single CLI and Python API.

**v0.10.0** | Python 3.12+ | 681 tests | Zero cost (all self-hosted or free APIs)

## Quick Start

```bash
# Install
git clone https://github.com/scaso01/monster-search.git
cd monster-search
pip install -e ".[dev]"

# Search (smart tiered default -- runs tier1 engines in parallel, ~15-60s)
monster-search "python asyncio best practices"

# Fast lookup (~3s)
monster-search --engine searxng "python asyncio"

# Deep search including slow AI engines (~2-5 min)
monster-search --deep "supply chain attacks 2026"

# Check service health
monster-search --health
```

## Engines

34 engines organized into 12 categories, executed in 3 priority tiers.

### Engine Table

| Engine | Category | Tier | Source | Time |
|--------|----------|------|--------|------|
| SearXNG | Web General | 1 | Docker (Monster :8080) | ~3-4s |
| Marginalia | Web General | 1 | External API | ~3s |
| mwmbl | Web General | 1 | External API | ~3s |
| Perplexity | Web AI | 1 | External (cookie auth) | ~30s |
| Synthesizer | Web AI | 1 | SearXNG + Crawl4AI + llama-server | ~30-60s |
| Vane | Web AI | 2 | Docker (Monster :3004) | ~2 min |
| Khoj | Web AI | 2 | Docker (Monster :42110) | ~2 min |
| Fyin | Web AI | 2 | CLI via SSH to Monster | ~2 min |
| Local Deep Researcher | Web AI | 3 | Docker (Monster :8300) | ~3-8 min |
| arXiv | Academic | 1 | External API | ~3s |
| Semantic Scholar | Academic | 1 | External API | ~3s |
| OpenAlex | Academic | 1 | External API | ~3s |
| Zoekt | Code | 1 | Local index | ~1s |
| OSV | Security | 1 | External API (osv.dev) | ~2s |
| deps.dev | Packages | 1 | External API | ~2s |
| Who-Dat | Domain/WHOIS | 1 | External API | ~1s |
| News (SearXNG) | News | 1 | Docker (Monster :8080) | ~5s |
| GNews | News | 1 | External RSS | ~2s |
| Archive.org | Archive | 1 | External API (CDX + catalog) | ~10s |
| YouTube | Video | 1 | yt-dlp + youtube-transcript-api | ~5-10s |
| grep.app | Code | 1 | External API | ~3s |
| GitHub Code Search | Code | 1 | gh CLI subprocess | ~5s |
| GitHub Repos | Code | 1 | gh CLI subprocess | ~3s |
| searchcode_repo | Code | opt-in | searchcode.com API (requires --repo) | ~2s |
| Hacker News | Community | 1 | Algolia API | ~2s |
| HuggingFace | AI/ML | 1 | HuggingFace Hub API | ~3s |
| Reddit | Community | 1 | Reddit API | ~3s |
| CheapShark | Shopping | 1 | CheapShark API | ~2s |
| SlickDeals | Shopping | 1 | SlickDeals API | ~3s |
| Crawl4AI | Utility | -- | Docker (Monster :11235) | ~15s |
| changedetection.io | Utility | -- | Docker (Monster :8086) | -- |

**Notes:**
- Tier 1 engines run on every query by default (always-on + router-gated specialists).
- Tier 2 engines auto-promote when tier 1 results are sparse (< 3 results).
- Tier 3 engines only run with `--deep` or for deep-research queries.
- Crawl4AI takes URLs (page extraction), not queries. changedetection.io monitors URL changes.
- Meilisearch runs as a background result cache (not a search engine).
- Router-gated engines (academic, security, packages, code, WHOIS, archive) activate only when the query matches their category via regex classification.

### Categories

| Category | Engines | Trigger |
|----------|---------|---------|
| Web General | searxng, marginalia, mwmbl | Always on |
| Web AI | perplexity, synthesizer, vane, khoj, fyin, local_researcher | Always on (tier1) / promoted (tier2) / deep (tier3) |
| Academic | arxiv, semantic_scholar, openalex | Query contains paper/research/arxiv/doi keywords |
| Code | zoekt, grepapp, github_code, github_repos | Query contains code constructs (.py, func, class, etc.) |
| Security | osv | Query contains CVE/GHSA/vulnerability keywords |
| Packages | deps | Query contains ecosystem prefix (npm:, pypi:, cargo:, etc.) |
| Domain/WHOIS | whodat | Query contains domain name or IP address |
| News | news, gnews | Query contains latest/breaking/headlines keywords |
| Archive | archive_org | Query contains wayback/archive/cached keywords or is a URL |
| Video | youtube | Query contains video/tutorial/watch keywords |
| Community | hackernews, reddit | Query contains discussion/forum/community keywords |
| AI/ML | huggingface | Query contains model/dataset/ML keywords |
| Shopping | cheapshark, slickdeals | Query contains buy/price/deal/discount keywords |

## Smart Tiered Execution

The default search mode (`monster-search "query"`) uses smart tiering:

1. **Classify** the query via regex patterns (security, academic, code, news, etc.)
2. **Run tier 1** engines in parallel (always-on + category-matched specialists)
3. **Auto-promote tier 2** if tier 1 yields fewer than 3 results
4. **Tier 3** only runs with `--deep` or for deep-research queries
5. **Fuse** results via weighted Reciprocal Rank Fusion (RRF)
6. **Deduplicate** via MinHash LSH content similarity

```
Tier 1 (fast, ~15-60s):  searxng, marginalia, news, gnews, perplexity,
                          synthesizer, + router-gated specialists
Tier 2 (medium, ~2 min): vane, khoj, fyin
Tier 3 (slow, ~3-8 min): local_researcher
```

### Circuit Breakers

Each engine has an independent circuit breaker. After repeated failures, the breaker opens and skips that engine for a cooldown period -- preventing one broken service from slowing down the entire search.

### RRF Fusion

Results from multiple engines are combined using weighted Reciprocal Rank Fusion. Each engine has a quality weight (perplexity: 1.0, searxng: 0.9, marginalia: 0.85, etc.). Duplicate URLs are merged with metadata from all contributing engines.

### MinHash Dedup

After fusion, a MinHash LSH pass removes near-duplicate content based on snippet similarity (Jaccard threshold 0.5, 3-gram shingles). Short texts fall back to URL-based dedup.

## CLI Reference

```
monster-search [OPTIONS] QUERY
```

### Core Options

| Flag | Description |
|------|-------------|
| `--engine NAME` | Run a specific engine or category alias |
| `--deep` | Include slow AI engines (tier2 + tier3) |
| `--category {general,news,images,science,files}` | SearXNG category filter |
| `--time-range {day,week,month,year}` | Time-filtered results |
| `--max-results N` | Override default (5) |
| `--json` | JSON output for piping |
| `--no-fuse` | Disable RRF fusion (legacy first-occurrence dedup) |
| `--model MODEL` | Override LLM model for AI engines |
| `--benchmark` | Benchmark engines with timing table |
| `--health` | Check service health status |

### Engine and Category Aliases

```bash
# Individual engines
monster-search --engine searxng "query"
monster-search --engine marginalia "query"
monster-search --engine perplexity "query"
monster-search --engine synthesizer "query"       # alias: --engine synth
monster-search --engine vane "query"
monster-search --engine khoj "query"
monster-search --engine fyin "query"
monster-search --engine local_researcher "query"
monster-search --engine news "topic"
monster-search --engine gnews "topic"
monster-search --engine archive_org "query or URL"
monster-search --engine crawl "https://url"        # page extraction (URL only)
monster-search --engine arxiv "transformer"
monster-search --engine semantic_scholar "attention"
monster-search --engine openalex "machine learning"
monster-search --engine osv "pypi:jinja2"
monster-search --engine deps "npm:express"
monster-search --engine whodat "example.com"
monster-search --engine zoekt "func main"

# Category aliases (run grouped engines in parallel)
monster-search --engine academic "transformer"     # arxiv + semantic_scholar + openalex
monster-search --engine security "pypi:jinja2"     # osv
monster-search --engine packages "npm:express"     # deps
monster-search --engine code "func main"           # zoekt
monster-search --engine whois "example.com"        # whodat

# Full sweep
monster-search --engine all "query"                # all 34 engines (~2-5 min)
```

### URL Change Monitoring

```bash
monster-search watch add "https://example.com" --tag news
monster-search watch list
monster-search watch list --tag news
monster-search watch check UUID
monster-search watch diff UUID
monster-search watch remove UUID
```

## Python API

```python
from monster_search import (
    SearXNGClient, AllEnginesClient, MarginaliaClient,
    PerplexityClient, Crawl4AIClient, LocalResearcherClient,
    NewsSearchClient, ArchiveOrgClient, VaneClient,
    ChangeDetectionClient, Config, check_health,
)
import asyncio

# Quick search (~3s)
results = SearXNGClient().search("python asyncio", max_results=5)

# AI synthesis (~30s)
message, results = PerplexityClient().search("latest frameworks")

# Smart tiered search (async, recommended default)
client = AllEnginesClient()
message, results = asyncio.run(client.smart_search("query"))

# Deep search including slow engines
message, results = asyncio.run(client.smart_search("query", include_slow=True))

# Page extraction (takes URL, not query)
content, results = Crawl4AIClient().search("https://example.com")

# URL monitoring
cd = ChangeDetectionClient()
cd.add_watch("https://github.com/trending", tag="github")

# Health check
status = check_health()
```

### SearchResult Model

Every engine returns `SearchResult` objects:

```python
@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str                          # engine name or "fused"
    engine: str | None = None            # upstream engine (e.g., "google")
    score: float | None = None
    published: str | None = None
    category: str | None = None
    sources: tuple[str, ...] | None = None   # engines that found this URL
    fused_score: float | None = None         # weighted RRF score
```

## Prerequisites

Docker containers on Monster (or any host):

| Service | Port | Purpose |
|---------|------|---------|
| [SearXNG](https://github.com/searxng/searxng) | :8080 | Privacy-respecting metasearch |
| [Vane](https://github.com/ItzCraworzyy/Perplexica) | :3004 | AI search (Perplexica fork, 4 JS patches) |
| [Local Deep Researcher](https://langchain-ai.github.io/langgraph/) | :8300 | LangGraph iterative research |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | :11235 | JS-rendered page extraction |
| [changedetection.io](https://github.com/dgtlmoon/changedetection.io) | :8086 | URL change monitoring |
| [Khoj](https://github.com/khoj-ai/khoj) | :42110 | AI chat/search (anonymous mode) |

External APIs (no containers):

| Service | Auth | Notes |
|---------|------|-------|
| [Marginalia](https://search.marginalia.nu/) | None | Independent web index, CC-BY-NC-SA 4.0 |
| [Perplexity](https://www.perplexity.ai/) | Session cookie | Manual refresh ~monthly |
| [arXiv](https://arxiv.org/) | None | Preprint search API |
| [Semantic Scholar](https://www.semanticscholar.org/) | None | Academic paper search |
| [OpenAlex](https://openalex.org/) | None | Open scholarly works |
| [OSV.dev](https://osv.dev/) | None | Vulnerability database |
| [deps.dev](https://deps.dev/) | None | Package metadata |
| [Who-Dat](https://github.com/MoeClub/whodat) | None | WHOIS lookup |
| [GNews](https://news.google.com/) | None | Google News RSS |
| [Archive.org](https://archive.org/) | None | CDX + Advanced Search |

Local services on Beast:
- **llama-server** (:8080) -- OpenAI-compatible LLM for Synthesizer engine
- **Fyin** -- CLI binary, invoked via SSH to Monster

## Architecture

```
src/monster_search/
├── __init__.py              # Public API exports
├── models.py                # SearchResult frozen dataclass (sources, fused_score)
├── config.py                # Config from MONSTER_* env vars
├── health.py                # Container health probes
├── cli.py                   # CLI entry point (argparse + watch routing)
├── benchmark.py             # Engine benchmarking (--benchmark)
├── fusion.py                # Weighted RRF with metadata merge
├── _tiered.py               # Tiered execution engine (tier1/2/3)
├── _router.py               # Regex query classifier (9 categories)
├── _breaker.py              # Per-engine circuit breakers
├── _dedup.py                # MinHash LSH content deduplication
├── __main__.py              # python -m support
└── clients/
    ├── _pool.py                  # Connection pool (reusable httpx clients)
    ├── searxng.py                # SearXNG JSON API (sync + async)
    ├── marginalia.py             # Marginalia independent search
    ├── perplexity_client.py      # Perplexity AI synthesis (cookie auth)
    ├── synthesizer.py            # AI synthesis (SearXNG + Crawl4AI + llama-server)
    ├── local_researcher.py       # Local Deep Researcher LangGraph REST
    ├── crawl4ai_client.py        # Crawl4AI page extraction
    ├── news.py                   # SearXNG news category, date-sorted
    ├── gnews.py                  # Google News RSS
    ├── archive_org.py            # Archive.org CDX + Advanced Search
    ├── vane.py                   # Vane AI search (dynamic provider IDs)
    ├── khoj.py                   # Khoj AI chat/search (anonymous)
    ├── fyin.py                   # Fyin search via SSH to Monster
    ├── arxiv.py                  # arXiv preprint search
    ├── semantic_scholar.py       # Semantic Scholar papers
    ├── openalex.py               # OpenAlex works
    ├── osv.py                    # OSV.dev vulnerabilities
    ├── deps.py                   # deps.dev package info
    ├── whodat.py                 # Who-Dat WHOIS lookup
    ├── zoekt.py                  # Zoekt code search
    ├── meilisearch_client.py     # Meilisearch result cache (background)
    ├── changedetection_client.py # changedetection.io URL monitoring
    └── all_engines.py            # Composite: tiered parallel + RRF fusion
```

## Configuration

All settings via `MONSTER_*` environment variables. Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MONSTER_SEARXNG_URL` | `http://localhost:8080` | SearXNG base URL |
| `MONSTER_DEFAULT_ENGINE` | `all` | Default CLI engine |
| `MONSTER_MAX_RESULTS` | `5` | Results per engine |
| `MONSTER_TIMEOUT` | `15` | HTTP timeout (SearXNG) |
| `MONSTER_PERPLEXITY_SESSION_TOKEN` | -- | Perplexity cookie (monthly refresh) |
| `MONSTER_PERPLEXITY_TIMEOUT` | `90` | Perplexity timeout |
| `MONSTER_VANE_URL` | `http://localhost:3004` | Vane AI search URL |
| `MONSTER_VANE_TIMEOUT` | `300` | Vane timeout |
| `MONSTER_KHOJ_URL` | `http://localhost:42110` | Khoj AI search URL |
| `MONSTER_KHOJ_TIMEOUT` | `300` | Khoj timeout |
| `MONSTER_SSH_HOST` | -- | Remote host for the SSH-routed engines (see below) |
| `MONSTER_FYIN_ENV_FILE` | -- | Optional env file sourced on that host before fyin |
| `MONSTER_FYIN_TIMEOUT` | `180` | Fyin SSH timeout |
| `MONSTER_LOCAL_RESEARCHER_URL` | `http://localhost:8300` | Local Deep Researcher URL |
| `MONSTER_LOCAL_RESEARCHER_TIMEOUT` | `600` | Local Researcher timeout |
| `MONSTER_CRAWL4AI_URL` | `http://localhost:11235` | Crawl4AI URL |
| `MONSTER_CRAWL4AI_TIMEOUT` | `60` | Crawl4AI timeout |
| `MONSTER_ARCHIVE_ORG_TIMEOUT` | `60` | Archive.org timeout |
| `MONSTER_CHANGEDETECTION_URL` | `http://localhost:8086` | changedetection.io URL |
| `MONSTER_CHANGEDETECTION_API_KEY` | -- | changedetection.io API key |

### Engines that run over SSH

Two engines shell out over SSH instead of speaking HTTP, and both are off until
you set `MONSTER_SSH_HOST` to something `ssh` accepts (`user@host`):

- **fyin** needs the `fyin` binary installed on that host. Without a host set it
  reports as disabled rather than failing at query time.
- **archive.org** queries go out directly by default. archive.org rate-limits
  some VPN exit IPs hard — a persistent HTTP 429 on CDX, sometimes a TCP
  timeout — so setting a host reroutes the request through `curl` there
  instead. Only do this if your own exit IP is one of the blocked ones.

Nothing else in the tool opens an SSH connection.

## Testing

```bash
# Unit tests (mocked HTTP, no containers needed)
pytest tests/ -v -m "not integration"

# Integration tests (requires running containers)
pytest tests/test_integration.py -v -m integration

# Lint
pyflakes src/monster_search/
```

Unit tests use `respx` for HTTP mocking and `unittest.mock` -- no network calls.

## License

MIT License. See [LICENSE](LICENSE) for details.
