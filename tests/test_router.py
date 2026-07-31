"""Tests for query router (regex classifier)."""

import pytest

from monster_search._router import (
    QueryCategory,
    classify_query,
    get_engines_for_category,
    route_query,
)


# --- classify_query ---

class TestClassifyQuery:
    """Test regex pattern matching for each category."""

    # SECURITY patterns
    @pytest.mark.parametrize("query", [
        "CVE-2024-1234",
        "GHSA-abc-def-ghi",
        "CWE-79 cross site scripting",
        "vulnerability in openssl",
        "exploit database",
        "latest malware threats",
        "ransomware attack vectors",
    ])
    def test_security(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.SECURITY

    # WHOIS patterns
    @pytest.mark.parametrize("query", [
        "example.com",
        "google.org",
        "test.io",
        "192.168.1.1",
        "8.8.8.8",
        "whois lookup",
    ])
    def test_whois(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.WHOIS

    # PACKAGE patterns
    @pytest.mark.parametrize("query", [
        "npm:express",
        "pypi:requests",
        "cargo:tokio",
        "crate:serde",
        "gem:rails",
        "nuget:newtonsoft",
        "maven:spring-boot",
        "pip:flask",
    ])
    def test_package(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.PACKAGE

    # CODE patterns
    @pytest.mark.parametrize("query", [
        "def main()",
        "class UserModel",
        "import asyncio",
        "struct Config",
        "async fn handle",
        "func main",
        "impl Display for",
        "interface Builder",
        "parser.py",
        "source code review",
    ])
    def test_code(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.CODE

    # ACADEMIC patterns
    @pytest.mark.parametrize("query", [
        "attention is all you need paper",
        "research paper on transformers",
        "nature journal study",
        "arxiv 2301.12345",
        "doi:10.1234/test",
        "preprint on quantum computing",
        "thesis defense strategies",
        "peer review process",
        "citation analysis",
    ])
    def test_academic(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.ACADEMIC

    # NEWS patterns
    @pytest.mark.parametrize("query", [
        "latest tech trends",
        "breaking news",
        "what happened today",
        "yesterday stock market",
        "this week in AI",
        "this month releases",
        "headlines about climate",
        "current events",
    ])
    def test_news(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.NEWS

    # ARCHIVE patterns
    @pytest.mark.parametrize("query", [
        "wayback machine",
        "archive this page",
        "cached version of site",
        "historical data from 2020",
        "snapshot of website",
        "internet archive search",
    ])
    def test_archive(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.ARCHIVE

    # VIDEO patterns
    @pytest.mark.parametrize("query", [
        "python tutorial",
        "how to tie a knot",
        "docker compose walkthrough",
        "kubernetes demo",
        "video about rust",
        "youtube channel reviews",
        "screencast of new features",
        "watch this presentation",
    ])
    def test_video(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.VIDEO

    # AI_ML patterns
    @pytest.mark.parametrize("query", [
        "huggingface bert model",
        "fine-tune llama 3",
        "fine tuning gpt",
        "llm inference optimization",
        "lora adapter training",
        "gguf quantization",
        "pretrained model for NER",
        "diffusion model training",
    ])
    def test_ai_ml(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.AI_ML

    # DEEP_RESEARCH patterns
    @pytest.mark.parametrize("query", [
        "compare rust vs go for backends",
        "analysis of market trends",
        "deep dive into kubernetes networking",
        "explain in detail how TLS works",
        "comprehensive guide to microservices",
        "thorough review of database options",
        "in-depth look at authentication",
        "survey of machine learning frameworks",
        "overview of cloud providers",
    ])
    def test_deep_research(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.DEEP_RESEARCH

    # ACADEMIC — bare "research" without "paper" suffix
    @pytest.mark.parametrize("query", [
        "research methodology",
        "climate research 2026 IPCC",
    ])
    def test_academic_bare_research(self, query: str) -> None:
        """Bare 'research' (without 'paper') must route to academic."""
        assert classify_query(query) == QueryCategory.ACADEMIC

    # GENERAL — comparison + year queries must NOT trigger academic
    @pytest.mark.parametrize("query", [
        "uv vs poetry vs pdm 2026",
        "vs code 2026 release",
        "python vs javascript 2026",
    ])
    def test_general_comparison_with_year(self, query: str) -> None:
        """Year suffix or 'vs' alone must not push a comparison query into academic."""
        assert classify_query(query) == QueryCategory.GENERAL

    # GENERAL (no pattern match)
    @pytest.mark.parametrize("query", [
        "best restaurants nearby",
        "weather forecast",
        "random stuff 12345",
    ])
    def test_general(self, query: str) -> None:
        assert classify_query(query) == QueryCategory.GENERAL

    # Edge cases
    def test_empty_query(self) -> None:
        assert classify_query("") == QueryCategory.GENERAL

    def test_whitespace_query(self) -> None:
        assert classify_query("   ") == QueryCategory.GENERAL

    def test_none_query(self) -> None:
        assert classify_query(None) == QueryCategory.GENERAL

    # Mixed signals — more specific wins
    def test_mixed_security_and_news(self) -> None:
        """SECURITY is more specific than NEWS, should win."""
        assert classify_query("latest CVE vulnerabilities") == QueryCategory.SECURITY

    def test_mixed_whois_and_general(self) -> None:
        assert classify_query("who owns example.com") == QueryCategory.WHOIS

    def test_case_insensitive(self) -> None:
        assert classify_query("CVE-2024-1234") == QueryCategory.SECURITY
        assert classify_query("cve-2024-1234") == QueryCategory.SECURITY
        assert classify_query("EXPLOIT in library") == QueryCategory.SECURITY


# --- get_engines_for_category ---

class TestGetEnginesForCategory:
    def test_security_engines(self) -> None:
        engines = get_engines_for_category(QueryCategory.SECURITY)
        assert "osv" in engines

    def test_academic_engines(self) -> None:
        engines = get_engines_for_category(QueryCategory.ACADEMIC)
        assert "arxiv" in engines
        assert "semantic_scholar" in engines
        assert "openalex" in engines

    def test_general_engines(self) -> None:
        engines = get_engines_for_category(QueryCategory.GENERAL)
        assert "searxng" in engines

    def test_returns_new_list(self) -> None:
        """Ensure we get a copy, not a reference to the internal list."""
        a = get_engines_for_category(QueryCategory.GENERAL)
        b = get_engines_for_category(QueryCategory.GENERAL)
        assert a is not b

    def test_all_categories_have_engines(self) -> None:
        for cat in QueryCategory:
            engines = get_engines_for_category(cat)
            assert len(engines) > 0, f"{cat} has no engines"


# --- route_query ---

class TestRouteQuery:
    def test_convenience_function(self) -> None:
        engines = route_query("CVE-2024-1234")
        assert "osv" in engines

    def test_general_fallback(self) -> None:
        engines = route_query("random stuff")
        assert "searxng" in engines
