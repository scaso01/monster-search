"""Local index of published fact-checks: build, match threshold, refresh fallback."""

from __future__ import annotations

import time

import pytest

from monster_search.clients import claimreview as cr


def _feed(claims):
    return {"dataFeedElement": [{"item": [{
        "claimReviewed": text, "url": f"https://www.politifact.com/factchecks/{i}",
        "author": {"name": "PolitiFact"}, "reviewRating": {"alternateName": "Pants on Fire"},
        "datePublished": "2021-01-06",
    }]} for i, text in enumerate(claims)]}


FILLER = [f"filler claim number {i} about tariffs" for i in range(60)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(cr, "MIN_ITEMS", 10)
    path = tmp_path / "cr.db"
    cr.build_index(_feed([
        "The 2021 Georgia Senate runoff and the 2020 presidential election were stolen.",
        "Brazil's presidential election was stolen.",
    ] + FILLER), path)
    return path


def test_matching_fact_check_is_returned_with_its_rating(db):
    hits = cr.ClaimReviewClient(db).search("The 2020 United States presidential election was stolen.")
    assert [h.url for h in hits] == ["https://www.politifact.com/factchecks/0"]
    assert hits[0].snippet.endswith("Rating: Pants on Fire.")
    assert hits[0].title.startswith("Fact-check by PolitiFact:")


def test_a_different_claim_sharing_half_the_words_is_not_offered(db):
    # "Brazil's presidential election was stolen" shares 3 of 6 keywords (0.5 < 0.6).
    urls = [h.url for h in cr.ClaimReviewClient(db).search("The 2020 United States presidential election was stolen.")]
    assert "https://www.politifact.com/factchecks/1" not in urls


def test_a_truncated_feed_is_rejected(tmp_path):
    with pytest.raises(cr.ClaimReviewUnavailable, match="looks broken"):
        cr.build_index(_feed(["one claim"]), tmp_path / "x.db")


def test_stale_index_is_used_when_refresh_fails(db, monkeypatch):
    monkeypatch.setattr(cr, "MAX_AGE_S", -1)

    def fail(path):
        raise OSError("offline")
    monkeypatch.setattr(cr, "refresh", fail)
    assert cr.ensure_index(db) == db


def test_no_index_and_no_download_fails_loudly(tmp_path, monkeypatch):
    def fail(path):
        raise OSError("offline")
    monkeypatch.setattr(cr, "refresh", fail)
    with pytest.raises(cr.ClaimReviewUnavailable):
        cr.ensure_index(tmp_path / "missing.db")


def test_fresh_index_is_not_rebuilt(db, monkeypatch):
    monkeypatch.setattr(cr, "refresh", lambda path: pytest.fail("should not refresh"))
    assert cr.ensure_index(db) == db
    assert cr._built(db) <= time.time()
