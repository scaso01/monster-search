"""CLI interface for monster-search."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys

# Force UTF-8 stdout/stderr on Windows to avoid cp1252 encoding crashes
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from dotenv import load_dotenv

from monster_search.clients.all_engines import AllEnginesClient
from monster_search.clients.searxng import SearXNGClient
from monster_search.config import Config
from monster_search.health import check_health_with_latency

if TYPE_CHECKING:
    # Only needed to resolve the return annotations below. The real import
    # stays inside the functions to keep CLI startup light.
    from monster_search.models import SearchResult

ENGINE_CHOICES = [
    # Web engines
    "searxng", "local_researcher",
    "marginalia", "mwmbl", "crawl", "perplexity", "news", "archive_org", "youtube", "github_repos", "all",
    # Category engines
    "semantic_scholar", "arxiv", "openalex",
    "osv", "deps", "gnews", "whodat", "zoekt",
    # Code search
    "grepapp", "github_code", "searchcode_repo",
    # Community/tech
    "hackernews", "huggingface", "reddit",
    # AI search engines
    "vane", "khoj", "fyin",
    # AI synthesizer (lightweight Perplexica/Vane replacement)
    "synthesizer", "synth",
    # Cache
    "meilisearch",
    # Shopping engines
    "shopping", "cheapshark", "slickdeals", "deals",
    "priceghost", "amazon_deals", "newegg",
    # Category aliases
    "academic", "security", "packages", "code", "whois", "video", "ai_ml",
]

# Category aliases → parallel engine groups
ENGINE_CATEGORIES = {
    "academic": ["arxiv", "semantic_scholar", "openalex"],
    "security": ["osv"],
    "packages": ["deps"],
    "code": ["zoekt", "grepapp", "github_code"],
    "ai_ml": ["huggingface"],
    "whois": ["whodat"],
    "video": ["youtube"],
    # searxng_shopping belongs here: _run_one has always had a branch for it and
    # the router's own SHOPPING category leads with it, but it was missing from
    # this list, so `--engine shopping` silently skipped the SearXNG shopping
    # category and that dispatch branch was unreachable.
    "shopping": [
        "searxng_shopping", "slickdeals", "cheapshark", "deals_rss",
        "priceghost", "amazon_deals", "newegg",
    ],
    "deals": ["slickdeals", "deals_rss", "amazon_deals"],
}


def _format_brief(results, message: str = "", numbered: bool = True, answer: str = "") -> str:
    """Format results as brief CLI output.

    When ``answer`` is non-empty it is rendered as an answer paragraph between
    the status line and the numbered result list, matching the target format:

        Smart search [general]: 7 results

        Tokio is a de facto asynchronous runtime... [1][2]
        [Sources: synthesizer]

        [1] Tokio - An asynchronous Rust runtime
            https://tokio.rs/
            ...
    """
    lines = []
    if message:
        lines.append(message)
        lines.append("")
    if answer:
        lines.append(answer)
        lines.append("[Sources: synthesizer]")
        lines.append("")
    for i, r in enumerate(results, 1):
        prefix = f"[{i}] " if numbered else ""
        lines.append(f"{prefix}{r.brief()}")
        lines.append("")
    return "\n".join(lines)


def _format_json(results, message: str = "", answer: str = "") -> str:
    """Format results as JSON.

    When ``answer`` is non-empty, a top-level ``"answer"`` key is included
    alongside ``"results"`` and ``"message"``.
    """
    data: dict = {"results": [asdict(r) for r in results]}
    if answer:
        data["answer"] = answer
    if message:
        data["message"] = message
    return json.dumps(data, indent=2)


def _handle_watch(argv: list[str]) -> None:
    """Handle watch subcommand for changedetection.io."""
    from monster_search.clients.changedetection_client import ChangeDetectionClient

    parser = argparse.ArgumentParser(prog="monster-search watch")
    sub = parser.add_subparsers(dest="action")

    add_p = sub.add_parser("add", help="Add a URL to watch")
    add_p.add_argument("url", help="URL to watch")
    add_p.add_argument("--tag", help="Tag for the watch")

    list_p = sub.add_parser("list", help="List all watches")
    list_p.add_argument("--tag", help="Filter by tag")

    check_p = sub.add_parser("check", help="Get latest snapshot")
    check_p.add_argument("uuid", help="Watch UUID")

    diff_p = sub.add_parser("diff", help="Get latest diff")
    diff_p.add_argument("uuid", help="Watch UUID")

    remove_p = sub.add_parser("remove", help="Remove a watch")
    remove_p.add_argument("uuid", help="Watch UUID")

    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return

    config = Config()
    client = ChangeDetectionClient(config=config)

    if args.action == "add":
        result = client.add_watch(args.url, tag=args.tag)
        print(json.dumps(result, indent=2))
    elif args.action == "list":
        watches = client.list_watches(tag=args.tag)
        if not watches:
            print("No watches found.")
            return
        for w in watches:
            tag_str = f" [{w.get('tag', '')}]" if w.get("tag") else ""
            print(f"  {w['uuid']}{tag_str}  {w.get('url', 'unknown')}")
    elif args.action == "check":
        # A newly added watch has no snapshot until changedetection.io fetches
        # the page, so say that rather than printing a blank line.
        print(client.get_latest(args.uuid) or "No snapshot recorded for this watch yet.")
    elif args.action == "diff":
        print(client.get_diff(args.uuid) or "No changes recorded for this watch yet.")
    elif args.action == "remove":
        ok = client.remove_watch(args.uuid)
        print("Removed." if ok else "Failed to remove.")


async def _run_category(
    category: str, query: str, config: Config, max_results: int,
    *, fuse: bool = True,
) -> tuple[str, list[SearchResult]]:
    """Run all engines in a category group in parallel."""
    from monster_search.fusion import fuse_results
    from monster_search.models import SearchResult

    engine_names = list(ENGINE_CATEGORIES[category])
    # Gate semantic_scholar out when no API key is configured.
    if "semantic_scholar" in engine_names and not config.semantic_scholar_api_key:
        engine_names = [e for e in engine_names if e != "semantic_scholar"]
    # Gate grepapp out when disabled (it rate-limits whole address ranges).
    if "grepapp" in engine_names and not config.grepapp_enabled:
        engine_names = [e for e in engine_names if e != "grepapp"]

    async def _run_one(name: str) -> list[SearchResult]:
        if name == "arxiv":
            from monster_search.clients.arxiv import ArxivClient
            return await ArxivClient(config=config).asearch(query, max_results=max_results)
        elif name == "semantic_scholar":
            from monster_search.clients.semantic_scholar import SemanticScholarClient
            return await SemanticScholarClient(config=config).asearch(query, max_results=max_results)
        elif name == "openalex":
            from monster_search.clients.openalex import OpenAlexClient
            return await OpenAlexClient(config=config).asearch(query, max_results=max_results)
        elif name == "osv":
            from monster_search.clients.osv import OsvClient
            return await OsvClient(config=config).asearch(query, max_results=max_results)
        elif name == "deps":
            from monster_search.clients.deps import DepsClient
            return await DepsClient(config=config).asearch(query)
        elif name == "zoekt":
            from monster_search.clients.zoekt import ZoektClient
            return await ZoektClient(config=config).asearch(query, max_results=max_results)
        elif name == "whodat":
            from monster_search.clients.whodat import WhoDatClient
            return await WhoDatClient(config=config).asearch(query)
        elif name == "youtube":
            from monster_search.clients.youtube import YouTubeClient
            return await YouTubeClient(config=config).asearch(query, max_results=max_results)
        elif name == "grepapp":
            from monster_search.clients.grepapp import GrepAppClient
            return await GrepAppClient(config=config).asearch(query, max_results=max_results)
        elif name == "github_code":
            from monster_search.clients.github_code import GithubCodeClient
            return await GithubCodeClient(config=config).asearch(query, max_results=max_results)
        elif name == "hackernews":
            from monster_search.clients.hackernews import HackerNewsClient
            return await HackerNewsClient(config=config).asearch(query, max_results=max_results)
        elif name == "huggingface":
            from monster_search.clients.huggingface import HuggingFaceClient
            return await HuggingFaceClient(config=config).asearch(query, max_results=max_results)
        elif name == "reddit":
            from monster_search.clients.reddit import RedditClient
            return await RedditClient(config=config).asearch(query, max_results=max_results)
        elif name == "slickdeals":
            from monster_search.clients.slickdeals import SlickdealsClient
            return await SlickdealsClient(config=config).asearch(query, max_results=max_results)
        elif name == "cheapshark":
            from monster_search.clients.cheapshark import CheapSharkClient
            return await CheapSharkClient(config=config).asearch(query, max_results=max_results)
        elif name == "deals_rss":
            from monster_search.clients.deals_rss import DealsRSSClient
            return await DealsRSSClient(config=config).asearch(query, max_results=max_results)
        elif name == "searxng_shopping":
            from monster_search.clients.shopping import ShoppingSearchClient
            return await ShoppingSearchClient(config=config).asearch(query, max_results=max_results)
        elif name == "priceghost":
            from monster_search.clients.priceghost import PriceGhostClient
            return await PriceGhostClient(config=config).asearch(query, max_results=max_results)
        elif name == "amazon_deals":
            from monster_search.clients.amazon_deals import AmazonDealsClient
            return await AmazonDealsClient(config=config).asearch(query, max_results=max_results)
        elif name == "newegg":
            from monster_search.clients.newegg import NeweggClient
            return await NeweggClient(config=config).asearch(query, max_results=max_results)
        return []

    tasks = {name: _run_one(name) for name in engine_names}
    results_by_engine_raw = await asyncio.gather(*tasks.values(), return_exceptions=True)

    succeeded = []
    failed = []
    engine_results: dict[str, list[SearchResult]] = {}

    for name, result in zip(tasks.keys(), results_by_engine_raw):
        if isinstance(result, BaseException):
            failed.append(name)
            print(f"WARNING: {name} failed: {result}", file=sys.stderr)
            continue
        succeeded.append(name)
        engine_results[name] = result

    if fuse and len(engine_results) > 0:
        combined = fuse_results(engine_results)
    else:
        seen: set[str] = set()
        combined = []
        for items in engine_results.values():
            for r in items:
                if r.url not in seen:
                    seen.add(r.url)
                    combined.append(r)

    parts = []
    if succeeded:
        parts.append(f"Succeeded: {', '.join(succeeded)}")
    if failed:
        parts.append(f"Failed: {', '.join(failed)}")

    return " | ".join(parts), combined


def main(argv: list[str] | None = None) -> None:
    # Load .env from CWD upward, or from package root (editable installs)
    if not load_dotenv():
        pkg_env = Path(__file__).resolve().parents[2] / ".env"
        if pkg_env.is_file():
            load_dotenv(pkg_env)

    if argv is None:
        argv = sys.argv[1:]

    # Route "watch" subcommand separately to avoid argparse positional conflicts
    if argv and argv[0] == "watch":
        _handle_watch(argv[1:])
        return

    parser = argparse.ArgumentParser(
        prog="monster-search",
        description="Unified search hub — web, academic, code, security, packages, WHOIS, news, video, AI, community, archive and shopping, smart tiered by default",
    )
    parser.add_argument("query", nargs="*", help="Search query (or URL for crawl engine)")
    parser.add_argument(
        "--engine",
        choices=ENGINE_CHOICES,
        default=None,
        help="Search engine (default: from config)",
    )
    parser.add_argument("--category", help="SearXNG category (general, news, images, science, files)")
    parser.add_argument("--time-range", help="Time range (day, week, month, year)")
    parser.add_argument("--focus", default="webSearch", help="Perplexica focus mode")
    parser.add_argument("--max-results", type=int, help="Max results to return")
    parser.add_argument("--model", help="Override LLM model (Perplexica)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument("--health", action="store_true", help="Check container health")
    parser.add_argument("--benchmark", action="store_true", help="Benchmark engines with timing table")
    parser.add_argument("--no-fuse", action="store_true", dest="no_fuse", help="Disable result fusion (use first-occurrence dedup)")
    parser.add_argument("--deep", action="store_true", help="Include slow AI engines (tier2+tier3)")
    parser.add_argument(
        "--repo",
        metavar="GIT_URL",
        help="Git repository URL (required for --engine searchcode_repo)",
    )

    args = parser.parse_args(argv)
    config = Config()

    if args.health:
        records = check_health_with_latency(config)
        for name, rec in records.items():
            indicator = "UP" if rec["up"] else "DOWN"
            latency_s = rec["latency_ms"] / 1000.0
            print(f"  {name}: {indicator} ({latency_s:.2f}s)")
        return

    if args.benchmark:
        if not args.query:
            parser.error("query is required for --benchmark")
        from monster_search.benchmark import run_benchmark, format_table, BENCHMARKABLE_ENGINES
        query = " ".join(args.query)
        if args.engine and args.engine not in ("all",) + tuple(ENGINE_CATEGORIES):
            engines = [args.engine]
        else:
            engines = BENCHMARKABLE_ENGINES
        results = run_benchmark(query, config, engines=engines)
        print(format_table(results))
        return

    if not args.query:
        parser.error("query is required (unless using --health)")

    query = " ".join(args.query)
    engine = args.engine
    max_results = args.max_results or config.max_results

    message = ""
    answer = ""
    results = []

    try:
        if engine is None:
            ac = AllEnginesClient(config=config)
            message, answer, results = asyncio.run(
                ac.smart_search(
                    query,
                    include_slow=args.deep,
                    max_results=max_results,
                    fuse=not args.no_fuse,
                )
            )
        elif engine == "all":
            ac = AllEnginesClient(config=config)
            message, answer, results = asyncio.run(
                ac.smart_search(
                    query,
                    include_slow=True,
                    max_results=max_results,
                    fuse=not args.no_fuse,
                )
            )
        elif engine == "searxng":
            client = SearXNGClient(config=config)
            results = client.search(
                query,
                category=args.category,
                time_range=args.time_range,
                max_results=max_results,
            )
        elif engine == "local_researcher":
            from monster_search.clients.local_researcher import LocalResearcherClient
            lr = LocalResearcherClient(config=config)
            message, results = lr.search(query)
        elif engine == "marginalia":
            from monster_search.clients.marginalia import MarginaliaClient
            mc = MarginaliaClient(config=config)
            results = mc.search(query, max_results=max_results)
        elif engine == "mwmbl":
            from monster_search.clients.mwmbl import MwmblClient
            results = MwmblClient(config=config).search(query, max_results=max_results)
        elif engine == "crawl":
            from monster_search.clients.crawl4ai_client import Crawl4AIClient
            cc = Crawl4AIClient(config=config)
            message, results = cc.search(query)
        elif engine == "perplexity":
            from monster_search.clients.perplexity_client import PerplexityClient
            pc = PerplexityClient(config=config)
            message, results = pc.search(query)
        elif engine == "news":
            from monster_search.clients.news import NewsSearchClient
            nc = NewsSearchClient(config=config)
            message, results = nc.search(
                query, max_results=max_results, time_range=args.time_range
            )
        elif engine == "archive_org":
            from monster_search.clients.archive_org import ArchiveOrgClient
            ao = ArchiveOrgClient(config=config)
            results = ao.search(query, max_results=max_results)
        elif engine == "semantic_scholar":
            if not config.semantic_scholar_api_key:
                print(
                    "error: semantic_scholar engine disabled — "
                    "set MONSTER_SEMANTIC_SCHOLAR_API_KEY in your environment "
                    "or in a .env file to enable",
                    file=sys.stderr,
                )
                sys.exit(1)
            from monster_search.clients.semantic_scholar import SemanticScholarClient
            sc = SemanticScholarClient(config=config)
            results = sc.search(query, max_results=max_results)
        elif engine == "arxiv":
            from monster_search.clients.arxiv import ArxivClient
            ac = ArxivClient(config=config)
            results = ac.search(query, max_results=max_results)
        elif engine == "openalex":
            from monster_search.clients.openalex import OpenAlexClient
            oa = OpenAlexClient(config=config)
            results = oa.search(query, max_results=max_results)
        elif engine == "osv":
            from monster_search.clients.osv import OsvClient
            oc = OsvClient(config=config)
            results = oc.search(query, max_results=max_results)
        elif engine == "deps":
            from monster_search.clients.deps import DepsClient
            dc = DepsClient(config=config)
            results = dc.search(query)
        elif engine == "gnews":
            from monster_search.clients.gnews import GNewsClient
            gc = GNewsClient(config=config)
            results = gc.search(query, max_results=max_results)
        elif engine == "whodat":
            from monster_search.clients.whodat import WhoDatClient
            wc = WhoDatClient(config=config)
            results = wc.search(query)
        elif engine == "zoekt":
            from monster_search.clients.zoekt import ZoektClient
            zc = ZoektClient(config=config)
            results = zc.search(query, max_results=max_results)
        elif engine == "vane":
            from monster_search.clients.vane import VaneClient
            vc = VaneClient(config=config)
            message, results = vc.search(query)
        elif engine == "khoj":
            from monster_search.clients.khoj import KhojClient
            kc = KhojClient(config=config)
            message, results = kc.search(query)
        elif engine == "fyin":
            from monster_search.clients.fyin import FyinClient
            fc = FyinClient(config=config)
            message, results = fc.search(query)
        elif engine in ("synthesizer", "synth"):
            from monster_search.clients.synthesizer import SynthesizerClient
            sc = SynthesizerClient(config=config)
            message, results = sc.search(query, deep=args.deep)
        elif engine == "youtube":
            from monster_search.clients.youtube import YouTubeClient
            yc = YouTubeClient(config=config)
            results = yc.search(query, max_results=max_results)
        elif engine == "grepapp":
            if not config.grepapp_enabled:
                print(
                    "error: grepapp engine disabled — "
                    "set MONSTER_GREPAPP_ENABLED=true in your environment "
                    "or in a .env file to enable. It is off by default because "
                    "grep.app rate-limits whole hosting and VPN address ranges, "
                    "so it fails instantly from many networks",
                    file=sys.stderr,
                )
                sys.exit(1)
            from monster_search.clients.grepapp import GrepAppClient
            gc = GrepAppClient(config=config)
            results = gc.search(query, max_results=max_results)
        elif engine == "github_code":
            from monster_search.clients.github_code import GithubCodeClient
            gc = GithubCodeClient(config=config)
            results = gc.search(query, max_results=max_results)
        elif engine == "github_repos":
            from monster_search.clients.github_repos import GithubReposClient
            gr = GithubReposClient(config=config)
            results = gr.search(query, max_results=max_results)
        elif engine == "searchcode_repo":
            if not args.repo:
                print(
                    "error: --repo <git-url> is required for --engine searchcode_repo\n"
                    "example: monster-search --engine searchcode_repo --repo https://github.com/owner/repo 'search terms'",
                    file=sys.stderr,
                )
                sys.exit(1)
            from monster_search.clients.searchcode_repo import SearchcodeRepoClient
            src = SearchcodeRepoClient(config=config)
            results = src.search(query, repository=args.repo, max_results=max_results)
        elif engine == "hackernews":
            from monster_search.clients.hackernews import HackerNewsClient
            hc = HackerNewsClient(config=config)
            results = hc.search(query, max_results=max_results)
        elif engine == "huggingface":
            from monster_search.clients.huggingface import HuggingFaceClient
            hf = HuggingFaceClient(config=config)
            results = hf.search(query, max_results=max_results)
        elif engine == "reddit":
            from monster_search.clients.reddit import RedditClient
            rc = RedditClient(config=config)
            results = rc.search(query, max_results=max_results)
        elif engine == "meilisearch":
            from monster_search.clients.meilisearch_client import MeilisearchClient
            mc = MeilisearchClient(config=config)
            results = mc.search(query, max_results=max_results)
        elif engine == "cheapshark":
            from monster_search.clients.cheapshark import CheapSharkClient
            cs = CheapSharkClient(config=config)
            results = cs.search(query, max_results=max_results)
        elif engine == "slickdeals":
            from monster_search.clients.slickdeals import SlickdealsClient
            sd = SlickdealsClient(config=config)
            results = sd.search(query, max_results=max_results)
        elif engine == "priceghost":
            from monster_search.clients.priceghost import PriceGhostClient
            pg = PriceGhostClient(config=config)
            results = pg.search(query, max_results=max_results)
        elif engine == "amazon_deals":
            from monster_search.clients.amazon_deals import AmazonDealsClient
            ad = AmazonDealsClient(config=config)
            results = ad.search(query, max_results=max_results)
        elif engine == "newegg":
            from monster_search.clients.newegg import NeweggClient
            ne = NeweggClient(config=config)
            results = ne.search(query, max_results=max_results)
        elif engine in ENGINE_CATEGORIES:
            message, results = asyncio.run(
                _run_category(engine, query, config, max_results, fuse=not args.no_fuse)
            )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (rate limited — retry later)" if code == 429 else ""
        print(f"error: {engine or 'smart'} returned HTTP {code}{hint}", file=sys.stderr)
        sys.exit(1)
    except httpx.TimeoutException:
        print(f"error: {engine or 'smart'} timed out", file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError:
        print(f"error: cannot connect to {engine or 'smart'} — is the service running?", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"error: {engine or 'smart'} failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if sys.stdout.encoding and sys.stdout.encoding.lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    if args.json_output:
        # JSON is for piping/programmatic use — return the full fused pool.
        print(_format_json(results, message, answer=answer))
    else:
        # smart_search/all now return the full multi-engine pool (tiered_search no
        # longer pre-truncates); cap the human-readable brief so the terminal isn't
        # flooded. Use --json or --max-results N for more.
        brief_results = results[:max_results] if max_results else results
        print(_format_brief(brief_results, message, answer=answer))
