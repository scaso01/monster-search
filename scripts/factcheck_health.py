"""Daily health check for the factcheck pipeline's fragile parts.

Renews the Perplexity login (so it never lapses while unused), refreshes the weekly
fact-check index, and proves both Google News link resolvers and the reliability
lists still work. Every result is logged; the
.ok file is touched only when everything passes, so the watchdog's staleness check
(job-freshness.json) pushes an alert to the phone the day something breaks.
"""

from __future__ import annotations

import datetime
import json
import sys
import traceback
from pathlib import Path

import feedparser
import httpx
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parent.parent
LOG = Path.home() / "Scripts" / "logs" / "factcheck-health.log"
OK_FILE = LOG.with_suffix(".ok")
# Read by the cc-chronicle Home pill; written on every run, pass or fail.
STATUS_FILE = LOG.with_suffix(".json")


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

    from monster_search.clients.gnews import DecodeRateLimited

    links = _google_news_links(3)
    client = GNewsClient()
    with httpx.Client(timeout=25) as http:
        try:
            ok = sum(client._decode(u, http).startswith("http") for u in links)
        except DecodeRateLimited as exc:
            raise RuntimeError("rate-limited by Google (429); the browser layer covers it meanwhile") from exc
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


def check_claimreview() -> str:
    from monster_search.clients import claimreview

    claimreview.ensure_index()  # rebuilds from the feed when the index is a week old
    hits = claimreview.ClaimReviewClient().search("The 2020 presidential election was stolen.")
    if not hits:
        raise RuntimeError("fact-check index returned nothing for a claim it has always matched")
    built = datetime.datetime.fromtimestamp(claimreview._built(claimreview._db_path()))
    return f"index built {built:%Y-%m-%d}, {len(hits)} match(es) for the canary claim"


CHECKS = {
    "perplexity": check_perplexity,
    "gnews-decode": check_gnews_decode,
    "gnews-browser": check_gnews_browser,
    "reliability": check_reliability,
    "claimreview": check_claimreview,
}


def main() -> int:
    load_dotenv(REPO / ".env")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    lines = []
    results = []
    for name, check in CHECKS.items():
        try:
            detail = check()
            lines.append(f"  ok   {name}: {detail}")
            results.append({"name": name, "ok": True, "detail": detail})
        except Exception as exc:  # each failure is logged and fails the run, never swallowed
            failed.append(name)
            detail = f"{type(exc).__name__}: {exc}"
            lines.append(f"  FAIL {name}: {detail}")
            lines.append("       " + traceback.format_exc().strip().splitlines()[-1])
            results.append({"name": name, "ok": False, "detail": detail[:300]})
    stamp = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
    STATUS_FILE.write_text(json.dumps({
        "run_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "state": "failing" if failed else "ok",
        "failed": failed,
        "checks": results,
    }, indent=1), encoding="utf-8")
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
