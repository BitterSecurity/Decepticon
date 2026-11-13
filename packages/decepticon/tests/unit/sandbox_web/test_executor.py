"""Unit tests for the browser-tier fallback executor (local node Playwright).

The in-sandbox executor shells the node real-Chrome templates; there is no
Playwright-MCP path. These tests cover the capability→executor mapping (pure)
and `run_playwright_fallback` with the node subprocess mocked.
"""

from __future__ import annotations

import json

import pytest
from decepticon_sandbox.web import executor
from decepticon_sandbox.web.executor import _pick_executor, run_playwright_fallback
from decepticon_sandbox.web.validators import SMALL_BODY_THRESHOLD, Verdict


def _envelope(html: str, final_url: str = "https://8.8.8.8/final") -> str:
    return json.dumps(
        {
            "html": html,
            "finalUrl": final_url,
            "status": 200,
            "cookies": [],
            "userAgent": "ua",
            "automation": "playwright-extra+stealth",
        }
    )


def test_pick_executor_maps_capabilities_to_local_chrome() -> None:
    # No Playwright-MCP in the sandbox: every tier resolves to a local template.
    assert (
        _pick_executor(["needs_real_tls_stack", "needs_js_exec"], "auto")
        == "playwright_real_chrome"
    )
    assert _pick_executor(["needs_js_exec"], "auto") == "playwright_real_chrome"  # MCP-only → local
    assert _pick_executor([], "mobile") == "playwright_mobile_chrome"
    assert _pick_executor(["needs_mobile_context"], "auto") == "playwright_mobile_chrome"


def test_node_unavailable_returns_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: False)
    att, html = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        force_executor="playwright_real_chrome",
    )
    assert att.verdict == Verdict.UNKNOWN.value
    assert html == ""
    assert att.phase == "fallback"
    assert att.error and "node" in att.error.lower()


def test_legacy_mcp_force_remaps_to_local_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    # A legacy profile may still name playwright_mcp in fallback_when_challenge;
    # remaps it to the local real-Chrome template.
    html = "x" * (SMALL_BODY_THRESHOLD + 50) + "<article id='c'>hi</article>"
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: True)
    monkeypatch.setattr(
        executor, "_run_node_template", lambda template, args, timeout=90: (0, _envelope(html), "")
    )
    att, out = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        success_selectors=["article#c"],
        force_executor="playwright_mcp",
    )
    assert att.executor == "playwright_real_chrome"  # remapped, not MCP
    assert att.verdict == Verdict.STRONG_OK.value
    assert att.url == "https://8.8.8.8/final"
    assert out == html


def test_final_private_url_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "x" * (SMALL_BODY_THRESHOLD + 50) + "<article id='c'>hi</article>"
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: True)
    monkeypatch.setattr(
        executor,
        "_run_node_template",
        lambda template, args, timeout=90: (
            0,
            _envelope(html, final_url="http://127.0.0.1:9876/secret"),
            "",
        ),
    )
    att, out = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        success_selectors=["article#c"],
        force_executor="playwright_real_chrome",
    )
    assert att.verdict == Verdict.BLOCKED.value
    assert att.error == "ssrf_blocked:ip_blocked:127.0.0.1"
    assert att.url == "http://127.0.0.1:9876/secret"
    assert out == ""


def test_final_out_of_scope_url_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "x" * (SMALL_BODY_THRESHOLD + 50) + "<article id='c'>hi</article>"
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: True)
    monkeypatch.setattr(
        executor,
        "_run_node_template",
        lambda template, args, timeout=90: (
            0,
            _envelope(html, final_url="https://8.8.4.4/final"),
            "",
        ),
    )
    att, out = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        success_selectors=["article#c"],
        force_executor="playwright_real_chrome",
        scope_check=lambda candidate: "example.com" in candidate,
    )
    assert att.verdict == Verdict.BLOCKED.value
    assert att.error == "ROE_REFUSED: final URL not in engagement scope"
    assert out == ""


def test_rendered_challenge_is_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: True)
    monkeypatch.setattr(
        executor,
        "_run_node_template",
        lambda template, args, timeout=90: (0, _envelope("Just a moment..."), ""),
    )
    att, _out = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        force_executor="playwright_real_chrome",
    )
    assert att.verdict == Verdict.CHALLENGE.value


def test_node_failure_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "_chrome_channel_available", lambda: True)
    monkeypatch.setattr(
        executor, "_run_node_template", lambda template, args, timeout=90: (1, "", "node boom")
    )
    att, html = run_playwright_fallback(
        "https://example.com/x",
        profile_id="unknown_challenge",
        force_executor="playwright_real_chrome",
    )
    assert att.verdict == Verdict.UNKNOWN.value
    assert html == ""
    assert att.error
