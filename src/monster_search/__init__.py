"""Monster Search — unified search hub for web, academic, code, security, and more."""

from __future__ import annotations

from monster_search.models import SearchResult
from monster_search.config import Config
from monster_search.clients.searxng import SearXNGClient
from monster_search.clients.perplexica import PerplexicaClient
from monster_search.clients.local_researcher import LocalResearcherClient
from monster_search.clients.marginalia import MarginaliaClient
from monster_search.clients.mwmbl import MwmblClient
from monster_search.clients.crawl4ai_client import Crawl4AIClient
from monster_search.clients.perplexity_client import PerplexityClient
from monster_search.clients.changedetection_client import ChangeDetectionClient
from monster_search.clients.news import NewsSearchClient
from monster_search.clients.archive_org import ArchiveOrgClient
from monster_search.clients.all_engines import AllEnginesClient
from monster_search.clients.semantic_scholar import SemanticScholarClient
from monster_search.clients.arxiv import ArxivClient
from monster_search.clients.openalex import OpenAlexClient
from monster_search.clients.osv import OsvClient
from monster_search.clients.deps import DepsClient
from monster_search.clients.gnews import GNewsClient
from monster_search.clients.whodat import WhoDatClient
from monster_search.clients.zoekt import ZoektClient
from monster_search.clients.vane import VaneClient
from monster_search.clients.khoj import KhojClient
from monster_search.clients.fyin import FyinClient
from monster_search.clients.meilisearch_client import MeilisearchClient
from monster_search.clients.synthesizer import SynthesizerClient
from monster_search.clients.youtube import YouTubeClient
from monster_search.clients.grepapp import GrepAppClient
from monster_search.clients.searchcode_repo import SearchcodeRepoClient
from monster_search.clients.github_code import GithubCodeClient
from monster_search.clients.github_repos import GithubReposClient
from monster_search.clients.hackernews import HackerNewsClient
from monster_search.clients.huggingface import HuggingFaceClient
from monster_search.clients.reddit import RedditClient
from monster_search.clients.shopping import ShoppingSearchClient
from monster_search.clients.cheapshark import CheapSharkClient
from monster_search.clients.slickdeals import SlickdealsClient
from monster_search.clients.deals_rss import DealsRSSClient
from monster_search.clients.priceghost import PriceGhostClient
from monster_search.clients.amazon_deals import AmazonDealsClient
from monster_search.clients.newegg import NeweggClient
from monster_search.health import check_health

__all__ = [
    "SearchResult",
    "Config",
    # Web engines
    "SearXNGClient",
    "PerplexicaClient",
    "LocalResearcherClient",
    "MarginaliaClient",
    "MwmblClient",
    "Crawl4AIClient",
    "PerplexityClient",
    "ChangeDetectionClient",
    "NewsSearchClient",
    "ArchiveOrgClient",
    "AllEnginesClient",
    # Academic engines
    "SemanticScholarClient",
    "ArxivClient",
    "OpenAlexClient",
    # Security
    "OsvClient",
    # Packages
    "DepsClient",
    # News
    "GNewsClient",
    # WHOIS/DNS
    "WhoDatClient",
    # Code search
    "ZoektClient",
    # Per-repo code intelligence
    "SearchcodeRepoClient",
    # AI search
    "VaneClient",
    "KhojClient",
    "FyinClient",
    # Cache
    "MeilisearchClient",
    # AI synthesizer
    "SynthesizerClient",
    # Video
    "YouTubeClient",
    # Code search
    "GrepAppClient",
    "GithubCodeClient",
    "GithubReposClient",
    "SearchcodeRepoClient",
    # Community/tech
    "HackerNewsClient",
    "HuggingFaceClient",
    "RedditClient",
    # Shopping engines
    "ShoppingSearchClient",
    "CheapSharkClient",
    "SlickdealsClient",
    "DealsRSSClient",
    "PriceGhostClient",
    "AmazonDealsClient",
    "NeweggClient",
    "check_health",
]
