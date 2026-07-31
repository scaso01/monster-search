"""Tests for the benchmark module."""

from __future__ import annotations

from unittest.mock import patch

from monster_search.benchmark import (
    BenchmarkResult,
    format_table,
    run_benchmark,
)
from monster_search.config import Config
from monster_search.models import SearchResult


def _fake_results():
    return [
        SearchResult(title="Test", url="https://example.com", snippet="test", source="searxng"),
    ]


def test_benchmark_result_fields():
    r = BenchmarkResult(engine="searxng", status="ok", result_count=5, elapsed_seconds=2.3)
    assert r.engine == "searxng"
    assert r.status == "ok"
    assert r.result_count == 5
    assert r.elapsed_seconds == 2.3
    assert r.error is None


def test_benchmark_result_with_error():
    r = BenchmarkResult(engine="osv", status="error", result_count=0, elapsed_seconds=0.1, error="boom")
    assert r.error == "boom"


@patch("monster_search.benchmark._run_engine")
def test_run_benchmark_success(mock_run):
    mock_run.return_value = ("ok", _fake_results())
    results = run_benchmark("test query", Config(), engines=["searxng", "marginalia"])
    assert len(results) == 2
    assert all(r.status == "ok" for r in results)
    assert all(r.result_count == 1 for r in results)


@patch("monster_search.benchmark._run_engine")
def test_run_benchmark_handles_error(mock_run):
    mock_run.side_effect = RuntimeError("connection refused")
    results = run_benchmark("test query", Config(), engines=["searxng"])
    assert len(results) == 1
    assert results[0].status == "error"
    assert "connection refused" in results[0].error


@patch("monster_search.benchmark._run_engine")
def test_run_benchmark_handles_timeout(mock_run):
    mock_run.side_effect = TimeoutError("timed out")
    results = run_benchmark("test query", Config(), engines=["searxng"])
    assert len(results) == 1
    assert results[0].status == "timeout"


def test_format_table():
    results = [
        BenchmarkResult(engine="searxng", status="ok", result_count=5, elapsed_seconds=2.3),
        BenchmarkResult(engine="osv", status="error", result_count=0, elapsed_seconds=0.1, error="boom"),
    ]
    table = format_table(results)
    assert "searxng" in table
    assert "osv" in table
    assert "2.3s" in table
    assert "boom" in table
    assert "TOTAL" in table
    assert "1/2 ok" in table


def test_format_table_empty():
    table = format_table([])
    assert "TOTAL" in table
    assert "0/0 ok" in table
