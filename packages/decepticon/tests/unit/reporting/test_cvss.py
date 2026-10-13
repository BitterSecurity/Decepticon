"""Tests for the deterministic CVSS 3.1 calculator tool.

Reference scores are the canonical values the FIRST.org calculator
produces — these pin the formula so a future refactor cannot silently
drift the severity that gets written into a report.
"""

from __future__ import annotations

import pytest

from decepticon.tools.reporting.cvss import (
    CVSSError,
    cvss_score_tool,
    parse_vector,
    score_vector,
    severity_band,
)


@pytest.mark.parametrize(
    "vector,expected",
    [
        # Canonical "worst case" — 9.8 Critical.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", 9.8),
        # Heartbleed-shape: network, confidentiality-only — 7.5 High.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N", 7.5),
        # No impact at all — 0.0 None.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N", 0.0),
        # The honest 8x8 webhook-SSRF rating: low-priv, blind, C:L — 4.3 Medium.
        ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N", 4.3),
        # Scope changed bumps the same impact up past the boundary.
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:H/I:H/A:H", 9.6),
    ],
)
def test_base_score_matches_reference(vector: str, expected: float) -> None:
    assert score_vector(vector)["base_score"] == expected


def test_30_prefix_is_accepted() -> None:
    # Agents (and the 8x8 form) sometimes emit a CVSS:3.0 prefix; the 3.1
    # base formula is identical, so we score it rather than reject it.
    out = score_vector("CVSS:3.0/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N")
    assert out["base_score"] == 4.3
    assert out["base_severity"] == "Medium"


def test_environmental_security_requirements_lower_score() -> None:
    # All-Low security requirements pull a 9.8 base down materially.
    out = score_vector("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/CR:L/IR:L/AR:L")
    assert out["has_environmental"] is True
    assert out["environmental_score"] < out["base_score"]


def test_environmental_neutral_when_requirements_medium_and_low_impact() -> None:
    # CR:M (weight 1.0) over a C:L/I:N/A:N finding leaves the score put —
    # exactly the 8x8 case (asset CR:M/IR:H/AR:H did not move 4.3).
    out = score_vector("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N/CR:M/IR:H/AR:H")
    assert out["environmental_score"] == 4.3


def test_severity_band_boundaries() -> None:
    assert severity_band(0.0) == "None"
    assert severity_band(0.1) == "Low"
    assert severity_band(3.9) == "Low"
    assert severity_band(4.0) == "Medium"
    assert severity_band(6.9) == "Medium"
    assert severity_band(7.0) == "High"
    assert severity_band(9.0) == "Critical"


def test_normalized_vector_orders_metrics() -> None:
    out = score_vector("CVSS:3.1/S:U/A:N/I:N/C:L/UI:N/PR:L/AC:L/AV:N")
    assert out["normalized_vector"] == ("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:U/C:L/I:N/A:N")


@pytest.mark.parametrize(
    "bad,fragment",
    [
        ("", "empty"),
        ("AV:N/AC:L", "must start with"),
        ("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H", "missing mandatory"),
        ("CVSS:3.1/AV:Z/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "illegal value"),
        ("CVSS:3.1/XX:N/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H", "unknown metric"),
    ],
)
def test_parse_rejects_malformed(bad: str, fragment: str) -> None:
    with pytest.raises(CVSSError) as exc:
        parse_vector(bad)
    assert fragment in str(exc.value)


def test_tool_returns_error_dict_not_raise() -> None:
    # The @tool wrapper must hand the agent a structured error, never throw.
    out = cvss_score_tool.invoke({"vector": "not-a-vector"})
    assert out["valid"] is False
    assert "error" in out


def test_tool_happy_path() -> None:
    out = cvss_score_tool.invoke({"vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"})
    assert out["valid"] is True
    assert out["base_score"] == 9.8
    assert out["base_severity"] == "Critical"


@pytest.mark.parametrize(
    "suffix",
    [
        "AV:P",
        "av:p",
        "AV:N",
        "E:H/E:U",
        "E:H/e:u",
        "CR:H/CR:L",
        "CR:H/cr:l",
        "MAV:N/MAV:P",
        "MAV:N/mav:p",
    ],
)
def test_tool_rejects_duplicate_metrics(suffix: str) -> None:
    vector = f"CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/{suffix}"
    out = cvss_score_tool.invoke({"vector": vector})
    assert out["valid"] is False
    assert "duplicate metric" in out["error"]
    assert set(out) == {"valid", "error"}


@pytest.mark.parametrize("version", ["3", "3.2", "3.10", "3.1extra", "30", "3.0extra"])
def test_tool_rejects_unsupported_versions(version: str) -> None:
    vector = f"CVSS:{version}/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
    out = cvss_score_tool.invoke({"vector": vector})
    assert out["valid"] is False
    assert "must start with" in out["error"]
    assert set(out) == {"valid", "error"}


@pytest.mark.parametrize(
    "vector",
    [
        "/CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1//AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1/AV:N//AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/",
    ],
)
def test_tool_rejects_empty_segments(vector: str) -> None:
    out = cvss_score_tool.invoke({"vector": vector})
    assert out["valid"] is False
    assert out["error"]
    assert set(out) == {"valid", "error"}
