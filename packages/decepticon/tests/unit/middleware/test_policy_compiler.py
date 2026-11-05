"""Tests for the RoE → OpenShell policy compiler."""

from __future__ import annotations

from pathlib import Path

import pytest

from decepticon.middleware.policy_compiler import (
    compile_openshell_policy,
    roe_binaries,
    roe_ports,
    sandbox_name,
)
from decepticon_core.types.roe import MachineEnforcement


def _compile(machine: dict) -> object:
    return compile_openshell_policy(MachineEnforcement.from_dict(machine))


def _roe_section(compiled) -> dict:
    return compiled.policy["network_policies"]["roe_in_scope"]


class TestCompilation:
    def test_host_and_wildcard_render_kernel_endpoints(self) -> None:
        compiled = _compile({"in_scope": ["Example.com", "*.staging.example.com"]})
        assert compiled.errors == ()
        endpoints = _roe_section(compiled)["endpoints"]
        by_host = {ep["host"]: ep for ep in endpoints}
        assert set(by_host) == {"example.com", "*.staging.example.com"}
        for endpoint in endpoints:
            assert endpoint["ports"] == sorted(roe_ports())
            assert endpoint["protocol"] == "rest"
            assert endpoint["enforcement"] == "enforce"
            assert endpoint["access"] == "full"
        # The runtime-invariant PTY grants ride on every compiled policy
        # (tmux-based exec substrate; verified live — the default-deny
        # filesystem policy blocks PTY allocation at the seccomp-notify layer).
        assert compiled.policy["filesystem_policy"] == {
            "include_workdir": True,
            "read_write": ["/dev/ptmx", "/dev/tty", "/dev/pts"],
        }
        # Static confinement (landlock/process) is intentionally not part of
        # the v1 network-boundary conversion — image defaults stay in force.
        assert "landlock" not in compiled.policy

    def test_cidr_renders_hostless_proxy_endpoint(self) -> None:
        compiled = _compile({"in_scope": ["10.10.0.0/24"]})
        assert compiled.errors == ()
        endpoint = _roe_section(compiled)["endpoints"][0]
        assert endpoint["allowed_ips"] == ["10.10.0.0/24"]
        assert "host" not in endpoint
        assert "protocol" not in endpoint

    def test_osint_allowance_section_present(self, monkeypatch) -> None:
        monkeypatch.delenv("DECEPTICON_OSINT_EGRESS_HOSTS", raising=False)
        compiled = _compile({"in_scope": ["example.com"]})
        osint = compiled.policy["network_policies"]["osint_allowance"]
        hosts = {ep["host"] for ep in osint["endpoints"]}
        assert hosts == {"duckduckgo.com", "html.duckduckgo.com"}

    def test_deterministic_across_rule_order(self) -> None:
        one = _compile({"in_scope": ["a.example.com", "b.example.com", "c.example.com"]})
        two = _compile({"in_scope": ["c.example.com", "a.example.com", "b.example.com"]})
        assert one.canonical_hash == two.canonical_hash
        assert one.policy == two.policy

    def test_hash_is_sha256_of_canonical_mapping(self) -> None:
        import hashlib
        import json

        compiled = _compile({"in_scope": ["example.com"]})
        expected = hashlib.sha256(
            json.dumps(compiled.policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert compiled.canonical_hash == expected


class TestFailClosed:
    def test_empty_in_scope_refuses(self) -> None:
        compiled = _compile({"in_scope": []})
        assert compiled.errors
        assert compiled.policy == {}

    def test_forbidden_destination_covered_by_glob_refuses(self) -> None:
        # Single-label glob: `*.internal` covers `a.internal` but never the
        # deep `metadata.google.internal` — the kernel deny there comes from
        # default-deny, matching the parser's forbidden check.
        compiled = _compile(
            {
                "in_scope": ["*.internal"],
                "forbidden_destinations": ["a.internal"],
            }
        )
        assert any("forbidden" in error for error in compiled.errors)

    def test_deep_forbidden_host_not_covered_by_single_label_glob(self) -> None:
        compiled = _compile(
            {
                "in_scope": ["*.internal"],
                "forbidden_destinations": ["metadata.google.internal"],
            }
        )
        assert compiled.errors == ()

    def test_forbidden_destination_covered_by_cidr_refuses(self) -> None:
        compiled = _compile({"in_scope": ["169.254.0.0/16"]})
        assert any("forbidden" in error for error in compiled.errors)

    def test_sensitive_tld_exact_host_refuses(self) -> None:
        compiled = _compile({"in_scope": ["test.gov"]})
        assert any("sensitive" in error for error in compiled.errors)

    def test_sensitive_tld_wildcard_refuses(self) -> None:
        compiled = _compile({"in_scope": ["*.mil"]})
        assert any("sensitive" in error for error in compiled.errors)

    def test_allow_sensitive_tlds_compiles(self) -> None:
        compiled = _compile({"in_scope": ["test.gov"], "allow_sensitive_tlds": True})
        assert compiled.errors == ()

    def test_out_of_scope_overlap_refuses(self) -> None:
        compiled = _compile({"in_scope": ["*.example.net"], "out_of_scope": ["a.example.net"]})
        assert any("out_of_scope" in error for error in compiled.errors)

    def test_non_leading_glob_refuses(self) -> None:
        compiled = _compile({"in_scope": ["api-*.example.com"]})
        assert any("kernel-proven" in error for error in compiled.errors)

    def test_non_default_port_url_refuses(self) -> None:
        compiled = _compile({"in_scope": ["http://example.com:8080/path"]})
        assert any("URL form" in error for error in compiled.errors)


class TestPortsAndBinaries:
    def test_url_derived_port_joins_pin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OPENSHELL_ROE_PORTS", "443")
        # A pathless default-port URL stays a host rule and contributes its
        # explicit port to the pinned set; URLs with paths are parser-dead
        # rules and refuse compilation instead.
        compiled = _compile({"in_scope": ["http://example.com:80"]})
        endpoint = _roe_section(compiled)["endpoints"][0]
        assert endpoint["host"] == "example.com"
        assert endpoint["ports"] == [80, 443]

    def test_invalid_port_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OPENSHELL_ROE_PORTS", "http")
        with pytest.raises(ValueError, match="invalid port"):
            roe_ports()

    def test_binaries_scoped_when_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OPENSHELL_ROE_BINARIES", "/usr/bin/curl, /usr/bin/python3")
        compiled = _compile({"in_scope": ["example.com"]})
        assert roe_binaries() == ("/usr/bin/curl", "/usr/bin/python3")
        assert _roe_section(compiled)["binaries"] == [
            {"path": "/usr/bin/curl"},
            {"path": "/usr/bin/python3"},
        ]

    def test_binaries_default_is_parity_any_process_glob(self) -> None:
        # Verified live on 0.1.6: an omitted or empty binaries field permits
        # nothing — the any-process parity requires the explicit ** glob.
        compiled = _compile({"in_scope": ["example.com"]})
        assert _roe_section(compiled)["binaries"] == [{"path": "**"}]


class TestSandboxName:
    def test_name_binds_workspace_and_roe(self, tmp_path: Path) -> None:
        compiled = _compile({"in_scope": ["example.com"]})
        name = sandbox_name(str(tmp_path), compiled.canonical_hash)
        assert name == sandbox_name(str(tmp_path), compiled.canonical_hash)
        assert name.startswith("dcpt-")
        assert compiled.canonical_hash[:8] in name

    def test_name_is_dns_label(self, tmp_path: Path) -> None:
        weird = tmp_path / "Engagement!! With Spaces"
        weird.mkdir()
        compiled = _compile({"in_scope": ["example.com"]})
        name = sandbox_name(str(weird), compiled.canonical_hash)
        assert name == name.lower()
        assert "!" not in name and " " not in name
        assert len(name) <= 63
        assert name[0].isalnum() and name[-1].isalnum()


class TestMixedScopeGrammar:
    def test_mixed_host_and_cidr_compiles_one_policy(self) -> None:
        # Named endpoints and hostless CIDR endpoints authorize disjoint
        # destination classes; gateways carrying the class-separation fix
        # (upstream NVIDIA/OpenShell#5193) accept the combined policy.
        compiled = _compile({"in_scope": ["example.com", "10.10.0.0/24"]})
        assert compiled.errors == ()
        endpoints = compiled.policy["network_policies"]["roe_in_scope"]["endpoints"]
        hosts = {ep.get("host") for ep in endpoints}
        assert hosts == {"example.com", None}
        cidr = next(ep for ep in endpoints if "allowed_ips" in ep)
        assert cidr["allowed_ips"] == ["10.10.0.0/24"]
        assert cidr["enforcement"] == "enforce"

    def test_cidr_only_compiles_with_osint_allowance(self) -> None:
        compiled = _compile({"in_scope": ["10.10.0.0/24"]})
        assert compiled.errors == ()
        sections = compiled.policy["network_policies"]
        assert "osint_allowance" in sections
        endpoint = sections["roe_in_scope"]["endpoints"][0]
        assert endpoint["allowed_ips"] == ["10.10.0.0/24"]
        assert endpoint["enforcement"] == "enforce"


@pytest.mark.parametrize(
    "scope,excluded,witness",
    [
        ("a.example.com", "*.example.com", "a.example.com"),
        ("*.example.com", "api-*.example.com", "api-test.example.com"),
        ("*.example.com", "*.example.com", "one.example.com"),
        ("10.0.0.0/8", "10.1.0.0/16", "10.1.2.3"),
        ("10.1.0.0/16", "10.0.0.0/8", "10.1.2.3"),
        ("10.1.2.3", "10.1.0.0/16", "10.1.2.3"),
        ("2001:db8::/32", "2001:db8:1::/48", "2001:db8:1::1"),
    ],
)
def test_excluded_set_intersections_refuse(scope, excluded, witness):
    from decepticon_core.types.roe import evaluate_target

    rules = MachineEnforcement.from_dict({"in_scope": [scope], "out_of_scope": [excluded]})
    assert not evaluate_target(witness, rules).allow
    assert compile_openshell_policy(rules).errors


@pytest.mark.parametrize(
    "scope,excluded",
    [
        ("*.example.com", "*.other.com"),
        ("*.example.com", "*.deep.example.com"),
        ("10.0.0.0/16", "10.1.0.0/16"),
        ("2001:db8::/32", "10.1.0.0/16"),
    ],
)
def test_disjoint_exclusions_still_compile(scope, excluded):
    assert not _compile({"in_scope": [scope], "out_of_scope": [excluded]}).errors


@pytest.mark.parametrize("tld", ["gov", "mil", "edu", "int"])
def test_deep_sensitive_wildcard_refuses_unless_operator_opted_in(tld):
    scope = "*.deep.agency." + tld
    assert _compile({"in_scope": [scope]}).errors
    assert not _compile({"in_scope": [scope], "allow_sensitive_tlds": True}).errors


def test_long_workspace_and_same_basename_never_drop_identity_or_policy(tmp_path):
    first = str(tmp_path / "a" / ("x" * 80))
    second = str(tmp_path / "b" / ("x" * 80))
    names = {
        sandbox_name(first, "a" * 64),
        sandbox_name(first, "b" * 64),
        sandbox_name(second, "a" * 64),
    }
    assert len(names) == 3
    # The OpenShell proto caps sandbox names at 19 chars (verified live).
    assert all(len(name) <= 19 for name in names)
    # The policy digest stays as the name suffix (truncated to the budget).
    assert sandbox_name(first, "a" * 64).endswith(("a" * 8))
    # The workspace digest keeps same-basename engagements distinct.
    assert (
        sandbox_name(first, "a" * 64).split("-")[1] != sandbox_name(second, "a" * 64).split("-")[1]
    )


@pytest.mark.parametrize(
    "denial",
    [{"out_of_scope": ["*.duckduckgo.com"]}, {"forbidden_destinations": ["duckduckgo.com"]}],
)
def test_osint_allowance_cannot_reintroduce_explicit_denial(denial):
    compiled = _compile({"in_scope": ["example.com"], **denial})
    assert not compiled.errors
    hosts = {
        entry["host"]
        for entry in compiled.policy["network_policies"]["osint_allowance"]["endpoints"]
    }
    if "out_of_scope" in denial:
        assert "html.duckduckgo.com" not in hosts
    else:
        assert "duckduckgo.com" not in hosts
