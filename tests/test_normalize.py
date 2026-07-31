"""Tests for URL normalization."""

from __future__ import annotations

from monster_search._normalize import normalize_url, urls_match


def test_strip_utm_params():
    url = "https://example.com/page?utm_source=google&utm_medium=cpc&q=test"
    result = normalize_url(url)
    assert "utm_source" not in result
    assert "utm_medium" not in result
    assert "q=test" in result


def test_strip_fbclid():
    url = "https://example.com/page?fbclid=abc123&q=test"
    result = normalize_url(url)
    assert "fbclid" not in result
    assert "q=test" in result


def test_strip_gclid():
    url = "https://example.com/page?gclid=abc123"
    result = normalize_url(url)
    assert "gclid" not in result


def test_remove_www():
    url = "https://www.example.com/page"
    result = normalize_url(url)
    assert "www." not in result
    assert "example.com" in result


def test_remove_trailing_slash():
    url = "https://example.com/page/"
    result = normalize_url(url)
    assert result.endswith("/page")


def test_keep_root_slash():
    url = "https://example.com/"
    result = normalize_url(url)
    assert result == "https://example.com/"


def test_remove_fragment():
    url = "https://example.com/page#section1"
    result = normalize_url(url)
    assert "#" not in result


def test_https_preferred():
    url = "http://example.com/page"
    result = normalize_url(url)
    assert result.startswith("https://")


def test_lowercase_hostname():
    url = "https://EXAMPLE.COM/Page"
    result = normalize_url(url)
    assert "example.com" in result
    # Path case is preserved
    assert "/Page" in result


def test_sort_query_params():
    url = "https://example.com/?z=1&a=2&m=3"
    result = normalize_url(url)
    # Params should be sorted alphabetically
    assert result.index("a=2") < result.index("m=3") < result.index("z=1")


def test_urls_match_same_url():
    assert urls_match("https://example.com", "https://example.com")


def test_urls_match_www_difference():
    assert urls_match("https://www.example.com/page", "https://example.com/page")


def test_urls_match_tracking_params():
    url1 = "https://example.com/page?utm_source=twitter"
    url2 = "https://example.com/page"
    assert urls_match(url1, url2)


def test_urls_match_http_vs_https():
    assert urls_match("http://example.com/page", "https://example.com/page")


def test_urls_match_trailing_slash():
    assert urls_match("https://example.com/page/", "https://example.com/page")


def test_urls_no_match_different_paths():
    assert not urls_match("https://example.com/a", "https://example.com/b")


def test_strip_ref_and_source_params():
    url = "https://example.com/page?ref=sidebar&source=homepage&id=42"
    result = normalize_url(url)
    assert "ref=" not in result
    assert "source=" not in result
    assert "id=42" in result


def test_preserve_port():
    url = "https://example.com:8080/page"
    result = normalize_url(url)
    assert ":8080" in result


def test_strip_standard_ports():
    url1 = normalize_url("https://example.com:443/page")
    url2 = normalize_url("https://example.com/page")
    assert url1 == url2
