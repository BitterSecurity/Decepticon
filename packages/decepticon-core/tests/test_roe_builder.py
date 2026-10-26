"""build_engagement_roe — mechanical RoE generation from engagement creation data."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from decepticon_core.types.roe import (
    MachineEnforcement,
    build_engagement_roe,
    evaluate_target,
)

_VECTORS = json.loads(
    (Path(__file__).parent / "testdata" / "roe-target-vectors.json").read_text(encoding="utf-8")
)


def _rules(targets, out_of_scope=()):
    roe = build_engagement_roe("eng", targets, out_of_scope)
    return MachineEnforcement.from_dict(roe["machine_enforcement"])


def test_enforcement_has_no_mode_field() -> None:
    roe = build_engagement_roe("eng", ["elice.io"])
    assert "mode" not in roe["machine_enforcement"]


def test_url_target_reduces_to_host() -> None:
    roe = build_engagement_roe("eng", ["https://elice.io/login?x=1"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]


def test_host_cidr_and_glob_pass_through() -> None:
    roe = build_engagement_roe("eng", ["elice.io", "10.0.0.0/24", "*.elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io", "10.0.0.0/24", "*.elice.io"]


def test_cidr_normalized_to_network_address() -> None:
    roe = build_engagement_roe("eng", ["10.0.0.5/24"])
    assert roe["machine_enforcement"]["in_scope"] == ["10.0.0.0/24"]


def test_wildcard_does_not_add_apex() -> None:
    roe = build_engagement_roe("eng", ["*.elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["*.elice.io"]
    rules = MachineEnforcement.from_dict(roe["machine_enforcement"])
    assert evaluate_target("api.elice.io", rules).allow
    assert not evaluate_target("elice.io", rules).allow
    assert not evaluate_target("a.b.elice.io", rules).allow


def test_wildcard_apex_deduped_with_explicit_target() -> None:
    roe = build_engagement_roe("eng", ["elice.io", "*.elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io", "*.elice.io"]


def test_malformed_wildcard_adds_nothing() -> None:
    roe = build_engagement_roe("eng", ["*", "*..elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["*"]


def test_scheme_relative_url_reduces_to_host() -> None:
    roe = build_engagement_roe("eng", ["//elice.io/path"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]


def test_embedded_list_targets_split() -> None:
    roe = build_engagement_roe("eng", ["elice.io, api.elice.io", "lms.elice.io redash.elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == [
        "elice.io",
        "api.elice.io",
        "lms.elice.io",
        "redash.elice.io",
    ]


def test_case_and_trailing_dot_normalized() -> None:
    roe = build_engagement_roe("eng", ["ELICE.io.", "App.ELICE.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io", "app.elice.io"]


def test_bare_ipv6_brackets_and_port_stripped() -> None:
    roe = build_engagement_roe("eng", ["[2001:db8::1]:8443"])
    assert roe["machine_enforcement"]["in_scope"] == ["2001:db8::1"]


def test_unicode_hostname_idna_encoded() -> None:
    roe = build_engagement_roe("eng", ["엘리스.io"])
    host = roe["machine_enforcement"]["in_scope"][0]
    assert host.startswith("xn--") and host.endswith(".io")


def test_git_scp_remote_reduces_to_host() -> None:
    roe = build_engagement_roe("eng", ["git@github.com:org/repo.git"])
    assert roe["machine_enforcement"]["in_scope"] == ["github.com"]


def test_dead_patterns_dropped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="decepticon_core.types.roe"):
        roe = build_engagement_roe("eng", ["*.", "elice..io", "*..elice.io", "elice.io"])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]
    assert any("no DNS/IP target can match" in r.message for r in caplog.records)


def test_dedupe_and_drop_empty() -> None:
    roe = build_engagement_roe("eng", ["elice.io", "https://elice.io", "  ", ""])
    assert roe["machine_enforcement"]["in_scope"] == ["elice.io"]


def test_no_in_scope_target_fails_closed() -> None:
    with pytest.raises(ValueError):
        build_engagement_roe("eng", [])
    with pytest.raises(ValueError):
        build_engagement_roe("eng", ["", "   "])


def test_generated_rules_enforce_scope() -> None:
    rules = _rules(["*.elice.io"], out_of_scope=["admin.elice.io"])
    # in-scope subdomain allowed, out-of-scope denied, unrelated host denied.
    assert evaluate_target("app.elice.io", rules).allow is True
    assert evaluate_target("admin.elice.io", rules).allow is False
    assert evaluate_target("evil.com", rules).allow is False


@pytest.mark.parametrize("case", _VECTORS["cases"], ids=lambda case: case["name"])
def test_golden_vectors(case: dict) -> None:
    """The shared vectors pin the builder and the Go launcher to one contract."""
    roe = build_engagement_roe("eng", case["targets"], case["out_of_scope"])
    assert roe["machine_enforcement"]["in_scope"] == case["in_scope"]
    assert roe["machine_enforcement"]["out_of_scope"] == case["out"]
