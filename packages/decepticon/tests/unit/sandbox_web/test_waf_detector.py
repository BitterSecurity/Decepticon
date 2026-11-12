"""Unit tests for the WAF-product detector (ranking, not single verdict)."""

from __future__ import annotations

from dataclasses import dataclass, field

from decepticon_sandbox.web import waf_detector
from decepticon_sandbox.web.waf_detector import (
    _load_profiles,
    detect,
    load_profile,
)


@dataclass
class _FakeResp:
    text: str = ""
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)


def test_profiles_load_from_yaml() -> None:
    profiles = _load_profiles()
    assert "akamai_bot_manager" in profiles
    assert "unknown_challenge" in profiles
    assert waf_detector.last_load_error() is None


def test_two_signals_is_high_confidence() -> None:
    resp = _FakeResp(
        text="... sec-if-cpt-container ...",
        cookies={"_abck": "x"},
    )
    hits = detect(resp)
    top = hits[0]
    assert top.profile_id == "akamai_bot_manager"
    assert top.confidence == 0.9
    assert any(s.startswith("cookie:") for s in top.signals)
    assert any(s.startswith("body:") for s in top.signals)


def test_single_signal_is_weak_confidence() -> None:
    resp = _FakeResp(cookies={"datadome": "1"}, text="")
    hits = detect(resp)
    dd = next(h for h in hits if h.profile_id == "datadome_probable")
    assert dd.confidence == 0.6


def test_header_wildcard_matches() -> None:
    resp = _FakeResp(headers={"X-Akamai-Transformed": "9"}, cookies={"bm_sz": "1"})
    hits = detect(resp)
    assert hits[0].profile_id == "akamai_bot_manager"
    assert any("header:X-Akamai-*" == s for s in hits[0].signals)


def test_no_signal_returns_unknown_challenge() -> None:
    hits = detect(_FakeResp(text="totally clean page", headers={"server": "nginx"}))
    assert len(hits) == 1
    assert hits[0].profile_id == "unknown_challenge"
    assert hits[0].confidence == 0.1


def test_ranking_is_sorted_by_confidence() -> None:
    # Akamai strong (2 signals) should outrank a 1-signal hit.
    resp = _FakeResp(
        text="Powered and protected by Akamai",
        cookies={"_abck": "x", "datadome": "y"},
    )
    hits = detect(resp)
    confidences = [h.confidence for h in hits]
    assert confidences == sorted(confidences, reverse=True)
    assert hits[0].profile_id == "akamai_bot_manager"


def test_load_profile_falls_back_to_unknown() -> None:
    prof = load_profile("nonexistent_waf")
    assert prof == _load_profiles()["unknown_challenge"]


def test_graceful_fallback_on_missing_yaml() -> None:
    profiles = _load_profiles(path="/nonexistent/waf_profiles.yaml")
    assert "unknown_challenge" in profiles
    assert profiles["unknown_challenge"]["notes"].startswith("in-code default")
    assert waf_detector.last_load_error() is not None
    assert "not found" in waf_detector.last_load_error()  # type: ignore[operator]
