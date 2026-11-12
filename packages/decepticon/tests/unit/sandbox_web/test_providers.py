"""Unit tests for web_search providers (allowlist + DDG parsing)."""

from __future__ import annotations

from decepticon_sandbox.web.providers import (
    DEFAULT_PROVIDER,
    ddg_query_url,
    is_allowed_provider,
    parse_ddg_html,
    unwrap_ddg_href,
)


def test_provider_allowlist() -> None:
    assert is_allowed_provider("duckduckgo")
    assert not is_allowed_provider("google")
    assert not is_allowed_provider("bing")
    assert DEFAULT_PROVIDER == "duckduckgo"


def test_query_url_quotes() -> None:
    url = ddg_query_url("claude code plugins")
    assert url.startswith("https://html.duckduckgo.com/html/?q=")
    assert "claude+code+plugins" in url


def test_unwrap_ddg_redirect() -> None:
    href = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpost&rut=abc"
    assert unwrap_ddg_href(href) == "https://example.com/post"


def test_unwrap_protocol_relative_ddg() -> None:
    href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa"
    assert unwrap_ddg_href(href) == "https://example.org/a"


def test_unwrap_non_ddg_host_passthrough() -> None:
    # A lookalike host must NOT be unwrapped (no smuggling via uddg).
    href = "https://evil.example.net/l/?uddg=https%3A%2F%2Fevil.test%2Fx"
    assert unwrap_ddg_href(href) == href


def test_unwrap_direct_url_passthrough() -> None:
    assert unwrap_ddg_href("https://example.com/direct") == "https://example.com/direct"


def test_parse_ddg_html_extracts_hits() -> None:
    body = """
    <div class="result">
      <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">
        First &amp; Title</a>
      <a class="result__snippet" href="x">A <b>snippet</b> here</a>
    </div>
    <div class="result">
      <a class="result__a" href="https://example.org/b">Second Title</a>
      <a class="result__snippet" href="y">Second snippet</a>
    </div>
    """
    hits = parse_ddg_html(body)
    assert len(hits) == 2
    assert hits[0].url == "https://example.com/a"
    assert hits[0].title == "First & Title"
    assert "snippet" in hits[0].snippet
    assert hits[1].url == "https://example.org/b"


def test_parse_ddg_skips_non_http() -> None:
    body = '<a class="result__a" href="javascript:void(0)">bad</a>'
    assert parse_ddg_html(body) == []
