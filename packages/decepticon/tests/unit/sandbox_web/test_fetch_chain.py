"""Unit tests for the fetch chain — grid logic + the Decepticon RoE per-hop
scope gate.

No real egress: ``_curl_probe`` is monkeypatched and the session pool (warmup),
Phase 0, the browser tier and per-host learning are disabled so the tests
exercise the curl grid + scope gate deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

import pytest

from decepticon.sandbox_web import fetch_chain
from decepticon.sandbox_web.fetch_chain import fetch
from decepticon.sandbox_web.validators import SMALL_BODY_THRESHOLD, Verdict


@dataclass
class _FakeResp:
    status_code: int = 200
    text: str = ""
    url: str = "https://example.com/"
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


_OK_BODY = "x" * (SMALL_BODY_THRESHOLD + 100)
_CHALLENGE_BODY = "Just a moment..."


def _patch_probe(monkeypatch: pytest.MonkeyPatch, fn) -> None:
    monkeypatch.setattr(fetch_chain, "_curl_probe", fn)
    # _jitter is a local closure inside _fetch_core; zero the jitter window via
    # env so the grid runs without real sleeps.
    monkeypatch.setenv("INSANE_JITTER_MS_MIN", "0")
    monkeypatch.setenv("INSANE_JITTER_MS_MAX", "0")
    # Disable the session pool so the root warmup never touches the network.
    monkeypatch.setenv("INSANE_NO_SESSION_POOL", "1")


def _fetch(url: str, **kw):
    # Engine knobs off by default so the grid + scope gate are isolated.
    kw.setdefault("enable_phase0", False)
    kw.setdefault("enable_learning", False)
    kw.setdefault("enable_playwright", False)
    return fetch(url, **kw)


def test_probe_success_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(url: str, **_kw: object):
        return _FakeResp(text=_OK_BODY, url=url), None

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://example.com/page")
    assert r.ok
    assert r.verdict == Verdict.WEAK_OK.value
    assert [a.phase for a in r.trace] == ["probe"]


def test_input_url_out_of_scope_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def probe(url: str, **_kw: object):
        called["n"] += 1
        return _FakeResp(text=_OK_BODY), None

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://evil.example.org/x", scope_check=lambda _u: False)
    assert not r.ok
    assert r.verdict == Verdict.BLOCKED.value
    assert "scope" in r.summary
    assert called["n"] == 0  # never hit the network
    assert r.trace[0].executor == "scope_gate"
    assert r.trace[0].reasons == ["roe_out_of_scope"]
    assert r.stop_reason == "roe_refused"


def test_challenge_then_grid_success(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    def probe(url: str, *, impersonate: str, referer: str, timeout: int = 20):
        state["calls"] += 1
        if state["calls"] == 1:
            return _FakeResp(text=_CHALLENGE_BODY, cookies={"_abck": "x"}), None
        return _FakeResp(text=_OK_BODY, url=url), None

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://www.example.com/p")
    assert r.ok
    assert state["calls"] >= 2
    assert any(a.phase == "grid" for a in r.trace)


def test_transform_hop_out_of_scope_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched_hosts: list[str] = []

    def probe(url: str, *, impersonate: str, referer: str, timeout: int = 20):
        fetched_hosts.append(urlsplit(url).hostname or "")
        return _FakeResp(text=_CHALLENGE_BODY, cookies={"_abck": "x"}), None

    _patch_probe(monkeypatch, probe)

    def scope(u: str) -> bool:
        return (urlsplit(u).hostname or "") != "m.example.com"

    r = _fetch("https://www.example.com/p", scope_check=scope)
    assert any(
        a.executor == "scope_gate" and urlsplit(a.url).hostname == "m.example.com" for a in r.trace
    )
    assert "m.example.com" not in fetched_hosts


def test_max_attempts_caps_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(url: str, **_kw: object):
        return _FakeResp(text=_CHALLENGE_BODY, cookies={"_abck": "x"}), None

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://www.example.com/p", max_attempts=3)
    grid = [a for a in r.trace if a.phase == "grid"]
    assert len(grid) <= 3
    assert r.stop_reason in ("budget", "exhausted")


def test_curl_unavailable_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(url: str, **_kw: object):
        return None, "curl_cffi not installed"

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://example.com/p", max_attempts=2)
    assert not r.ok
    assert r.trace[0].error == "curl_cffi not installed"


def test_failure_surfaces_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # The failure gate (R6) fields must be present on a give-up so the caller
    # can tell a give-up from true exhaustion.
    def probe(url: str, **_kw: object):
        return _FakeResp(text=_CHALLENGE_BODY, cookies={"_abck": "x"}), None

    _patch_probe(monkeypatch, probe)
    r = _fetch("https://www.example.com/p", max_attempts=3)
    assert not r.ok
    assert r.stop_reason
    assert isinstance(r.untried_routes, list)


def test_to_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(url: str, **_kw: object):
        return _FakeResp(text=_OK_BODY, url=url), None

    _patch_probe(monkeypatch, probe)
    d = _fetch("https://example.com/p").to_dict()
    # Validator-v2 / failure-gate schema: the gate fields must be serialized and
    # the raw content must NOT be (only its length).
    required = {
        "ok",
        "final_url",
        "verdict",
        "profile_used",
        "trace",
        "summary",
        "content_length",
        "grid_exhausted",
        "stop_reason",
        "untried_routes",
        "must_invoke_playwright_mcp",
    }
    assert required <= set(d)
    assert "content" not in d
    assert isinstance(d["trace"], list)
