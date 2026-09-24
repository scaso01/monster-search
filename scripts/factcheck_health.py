"""Daily health check for the factcheck pipeline's fragile parts.

Renews the Perplexity login (so it never lapses while unused), and proves both Google
News link resolvers and the reliability lists still work. Every result is logged; the
.ok file is touched only when everything passes, so the watchdog's staleness check
(job-freshness.json) pushes an alert to the phone the day something breaks.
"""

from __future__ import annotations

import datetime
import sys
import traceback
from pathlib import Path

import feedparser
import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
LOG = Path.home() / "Scripts" / "logs" / "factcheck-health.log"
OK_FILE = LOG.with_suffix(".ok")


def _google_news_links(n: int) -> list[str]:
    from monster_search.clients.gnews import GNEWS_RSS_BASE

    feed = feedparser.parse(httpx.get(GNEWS_RSS_BASE, timeout=20, params={
        "q": "NASA", "hl": "en-US", "gl": "US", "ceid": "US:en"}).text)
    links = [str(e.link) for e in feed.entries[:n]]
    if not links:
        raise RuntimeError("Google News RSS returned no items")
    return links


def check_perplexity() -> str:
    from monster_search.clients.perplexity_client import PerplexityClient
    from monster_search.config import Config

    expires = PerplexityClient(Config()).renew()
    return f"login renewed until {datetime.datetime.fromtimestamp(expires):%Y-%m-%d}"


def check_gnews_decode() -> str:
    from monster_search.clients.gnews import GNewsClient

    links = _google_news_links(3)
    client = GNewsClient()
    with httpx.Client(timeout=25) as http:
        ok = sum(client._decode(u, http).startswith("http") for u in links)
    if ok == 0:
        raise RuntimeError(f"batchexecute decoded 0/{len(links)} links")
    return f"decoded {ok}/{len(links)} links"


def check_gnews_browser() -> str:
    from monster_search.clients.gnews import _navigate

    links = _google_news_links(1)
    if not _navigate(links):
        raise RuntimeError("browser did not resolve the link")
    return "browser resolved 1/1 link"


def check_reliability() -> str:
    from monster_search.clients import reliability

    _, status = reliability.load()
    if status["status"] != "ok":
        raise RuntimeError(f"reliability lists {status}")
    return f"{status['domains']} rated domains"


CHECKS = {
    "perplexity": check_perplexity,
    "gnews-decode": check_gnews_decode,
    "gnews-browser": check_gnews_browser,
    "reliability": check_reliability,
}


def main() -> int:
    load_dotenv(REPO / ".env")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    lines = []
    for name, check in CHECKS.items():
        try:
            lines.append(f"  ok   {name}: {check()}")
        except Exception as exc:  # each failure is logged and fails the run, never swallowed
            failed.append(name)
            lines.append(f"  FAIL {name}: {type(exc).__name__}: {exc}")
            lines.append("       " + traceback.format_exc().strip().splitlines()[-1])
    stamp = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
    summary = "all passed" if not failed else "FAILED: " + ", ".join(failed)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} {summary}\n" + "\n".join(lines) + "\n")
    print(f"{stamp} {summary}\n" + "\n".join(lines))
    if failed:
        return 1
    OK_FILE.write_text(stamp + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
