"""Site reliability lists: parsing, worst-rating merge, host matching, cache fallback."""

from __future__ import annotations

import json
import time

import pytest

from monster_search.clients import reliability as rl

RSP = """{| class="wikitable"
|- class="s-gr" id="Reuters"
| [[Reuters]]
| {{WP:RSPSTATUS|gr}}
| notes
| {{WP:RSPUSES|reuters.com}}
|- class="s-gu" id="Fox News (politics)"
| [[Fox News]]
| {{WP:RSPSTATUS|gu}}
| notes
| {{WP:RSPUSES|foxnews.com|foxbusiness.com}}
|- class="s-nc" id="Fox News (other)"
| [[Fox News]]
| {{WP:RSPSTATUS|nc}}
| notes
| {{WP:RSPUSES|foxnews.com}}
|- class="s-gr" id="ABC"
| [[ABC News (United States)|ABC News (USA)]]
| {{WP:RSPSTATUS|gr}}
| notes
| {{WP:RSPUSES|abcnews.go.com}}
|- class="s-d" id="Daily Mail"
| [[Daily Mail]]
| {{WP:RSPSTATUS|d}}
| notes
| {{WP:RSPUSES|dailymail.co.uk|www.mailonline.com}}
|}"""

IFFY = """Domain,MBFC Fact,MBFC Bias,Wiki Fake
lowfacts.com,L,FN,
pinkslime.com,M,FN,1
mixedonly.com,M,FN,
"""


def _table():
    table: dict[str, dict] = {}
    rl.parse_rsp(RSP, table)
    rl.parse_iffy(IFFY, table)
    return table


def test_rsp_rows_parse_and_a_domain_keeps_its_worst_rating():
    t = _table()
    assert t["reuters.com"]["rating"] == "reliable"
    assert t["foxnews.com"]["rating"] == "unreliable"      # gu beats nc for the same domain
    assert t["foxbusiness.com"]["rating"] == "unreliable"
    assert t["mailonline.com"]["rating"] == "deprecated"   # www. stripped
    assert t["abcnews.go.com"]["why"] == "Wikipedia RSP: reliable (ABC News (USA))"


def test_iffy_uses_only_fact_based_listings():
    t = _table()
    assert t["lowfacts.com"]["rating"] == "low-credibility"
    assert t["pinkslime.com"]["rating"] == "low-credibility"
    assert "mixedonly.com" not in t


@pytest.mark.parametrize("url,rating", [
    ("https://www.reuters.com/world/x", "reliable"),
    ("https://abcnews.go.com/x", "reliable"),
    ("https://go.com/x", "unrated"),
    ("https://en.wikipedia.org/wiki/X", "unrated"),       # override: self-rating is internal
    ("https://www.instagram.com/p/x", "user-generated"),
    ("https://m.facebook.com/x", "user-generated"),
    ("https://unknown.example/x", "unrated"),
])
def test_rate_matches_most_specific_host(url, rating):
    assert rl.rate(url, _table())["rating"] == rating


def test_fresh_cache_is_used_without_downloading(tmp_path, monkeypatch):
    cache = tmp_path / "r.json"
    cache.write_text(json.dumps({"fetched": time.time(), "domains": {"a.com": {"rating": "reliable", "why": "x"}}}))
    monkeypatch.setattr(rl, "download", lambda: pytest.fail("should not download"))
    _, status = rl.load(cache)
    assert status == {"status": "ok", "domains": 1}


def test_stale_cache_beats_a_failed_download(tmp_path, monkeypatch):
    cache = tmp_path / "r.json"
    cache.write_text(json.dumps({"fetched": 0, "domains": {"a.com": {"rating": "reliable", "why": "x"}}}))

    def fail():
        raise OSError("offline")
    monkeypatch.setattr(rl, "download", fail)
    table, status = rl.load(cache)
    assert status["status"] == "stale" and "a.com" in table


def test_no_cache_and_no_download_fails_loudly(tmp_path, monkeypatch):
    def fail():
        raise OSError("offline")
    monkeypatch.setattr(rl, "download", fail)
    with pytest.raises(rl.ReliabilityUnavailable):
        rl.load(tmp_path / "missing.json")


def test_a_shrunken_list_is_treated_as_a_format_change(monkeypatch):
    monkeypatch.setattr(rl, "_fetch_rsp", lambda client: [RSP])

    class Resp:
        text = IFFY

        def raise_for_status(self):
            pass
    monkeypatch.setattr(rl.httpx.Client, "get", lambda self, url, **kw: Resp())
    with pytest.raises(rl.ReliabilityUnavailable, match="format changed"):
        rl.download()
