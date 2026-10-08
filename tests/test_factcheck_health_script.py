"""scripts/factcheck_health.py: Perplexity is optional for the health verdict."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from monster_search.clients import factcheck
from monster_search.config import Config

_SPEC = importlib.util.spec_from_file_location(
    "factcheck_health", Path(__file__).resolve().parent.parent / "scripts" / "factcheck_health.py"
)
fh = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fh)


def _r(name: str, ok: bool = True) -> dict:
    row = {"name": name, "ok": ok, "detail": ""}
    if not ok and name in fh.OPTIONAL_CHECKS:
        row["optional"] = True
    return row


def test_all_pass_is_ok():
    assert fh.verdict([_r("perplexity"), _r("claimreview")])["state"] == "ok"


def test_lapsed_perplexity_is_degraded_not_failing():
    v = fh.verdict([_r("perplexity", False), _r("claimreview")])
    assert v == {"state": "degraded", "failed": [], "warnings": ["perplexity"]}


def test_required_failure_is_failing_even_with_perplexity_down():
    v = fh.verdict([_r("perplexity", False), _r("gnews-decode", False), _r("claimreview")])
    assert v["state"] == "failing"
    assert v["failed"] == ["gnews-decode"]
    assert v["warnings"] == ["perplexity"]


def test_only_perplexity_is_optional():
    assert fh.OPTIONAL_CHECKS == {"perplexity"}


def test_factcheck_survives_lapsed_perplexity(monkeypatch):
    def lapsed(claim, config, n):
        raise RuntimeError("Perplexity login has lapsed; log in to perplexity.ai in the browser")

    monkeypatch.setattr(factcheck, "_FALLBACKS", {"perplexity": lapsed})
    found: list = []
    status = factcheck._run_fallbacks("claim", Config(), 3, found)
    assert status["perplexity"]["status"] == "error"
    assert "lapsed" in status["perplexity"]["error"]
    assert found == []
