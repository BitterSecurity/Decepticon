"""Tests for the Layer-2 egress policy compiler.

The compiler translates a ``MachineEnforcement`` block (the same scope
definition the parser-side guardrail reads) into a network-boundary
policy: an nftables connect-allowlist + a DNS allowlist. This is the
*authoritative* RoE enforcement point — a packet to an out-of-scope
host cannot leave the sandbox even if the command parser missed the
target. These tests pin the pure-logic compilation; the live apply is
exercised by the sandbox kernel against a running container.
"""

from __future__ import annotations

from decepticon.middleware.egress import (
    EgressPolicy,
    compile_egress_policy,
    render_dns_allowlist,
    render_nftables,
)
from decepticon_core.types.roe import MachineEnforcement


def _rules(**block) -> MachineEnforcement:
    return MachineEnforcement.from_dict(block)


# ── compile_egress_policy ────────────────────────────────────────────


def test_audit_mode_does_not_enforce():
    pol = compile_egress_policy(_rules(mode="audit", in_scope=["10.0.0.0/24"]))
    assert pol.enforce is False
    assert pol.default_drop is False


def test_warn_mode_does_not_enforce():
    pol = compile_egress_policy(_rules(mode="warn", in_scope=["10.0.0.0/24"]))
    assert pol.enforce is False


def test_enforce_with_in_scope_sets_default_drop():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    assert pol.enforce is True
    assert pol.default_drop is True
    assert "10.0.0.0/24" in pol.allowed_cidrs


def test_in_scope_literal_ip_is_allowed_cidr():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["203.0.113.7"]))
    assert "203.0.113.7" in pol.allowed_cidrs


def test_in_scope_host_and_glob_go_to_allowed_hosts():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["*.acme.com", "prod.acme.com"]))
    # In-scope hosts are present (alongside the always-allowed OSINT providers).
    assert {"*.acme.com", "prod.acme.com"} <= set(pol.allowed_hosts)
    assert pol.allowed_cidrs == ()


def test_signed_root_url_compiles_to_host_allowlist():
    pol = compile_egress_policy(
        _rules(mode="enforce", in_scope=[{"target": "https://decepticon.red/", "type": "auto"}])
    )

    assert "decepticon.red" in pol.allowed_hosts
    assert "https://decepticon.red/" not in pol.allowed_hosts
    assert pol.default_drop is True


def test_path_specific_url_cannot_open_egress():
    pol = compile_egress_policy(
        _rules(
            mode="enforce", in_scope=[{"target": "https://decepticon.red/admin", "type": "auto"}]
        )
    )

    assert "decepticon.red" not in pol.allowed_hosts
    assert pol.default_drop is True


def test_url_exclusion_denies_host_even_with_path():
    pol = compile_egress_policy(
        _rules(
            mode="enforce",
            in_scope=["decepticon.red"],
            out_of_scope=[{"target": "https://decepticon.red/admin", "type": "auto"}],
        )
    )

    assert "decepticon.red" in pol.denied_hosts


def test_osint_search_providers_are_always_allowed():
    # web_search is OSINT (target-exempt); its provider hosts must be reachable
    # even under an enforcing in-scope allowlist, else web_search is dropped at
    # the network layer.
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["*.acme.com"]))
    assert {"duckduckgo.com", "html.duckduckgo.com"} <= set(pol.allowed_hosts)


def test_cloud_metadata_denied_by_default():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    assert "169.254.169.254" in pol.denied_cidrs
    assert {"metadata.google.internal"} <= set(pol.denied_hosts)


def test_cloud_metadata_opt_out_removes_default_denies():
    pol = compile_egress_policy(
        _rules(mode="enforce", in_scope=["10.0.0.0/24"], allow_cloud_metadata=True)
    )
    assert "169.254.169.254" not in pol.denied_cidrs


def test_out_of_scope_cidr_and_host_are_denied():
    pol = compile_egress_policy(
        _rules(
            mode="enforce",
            in_scope=["10.0.0.0/8"],
            out_of_scope=["10.1.2.0/24", "secret.acme.com"],
        )
    )
    assert "10.1.2.0/24" in pol.denied_cidrs
    assert {"secret.acme.com"} <= set(pol.denied_hosts)


def test_enforce_without_in_scope_does_not_default_drop():
    # No allowlist means no "drop everything not listed" — but explicit
    # denies (forbidden dests, out-of-scope) still apply.
    pol = compile_egress_policy(_rules(mode="enforce", out_of_scope=["evil.com"]))
    assert pol.enforce is True
    assert pol.default_drop is False
    assert {"evil.com"} <= set(pol.denied_hosts)


# ── render_nftables ──────────────────────────────────────────────────


def test_render_flushes_table_for_idempotent_reapply():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    script = render_nftables(pol)
    # Re-running the ruleset must replace, not stack — the table is
    # destroyed up-front (nft tolerates the table not existing yet).
    assert "table inet decepticon_egress" in script
    assert "delete table inet decepticon_egress" in script


def test_render_audit_mode_has_no_drop_policy():
    pol = compile_egress_policy(_rules(mode="audit"))
    script = render_nftables(pol)
    # Audit re-provision must clear any prior enforce rules, so the table
    # is still (re)defined, but it never drops.
    assert "policy drop" not in script
    assert "drop" not in script


def test_render_enforce_emits_drop_policy_and_management_allow():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    script = render_nftables(pol, management_cidrs=["172.20.0.0/16"])
    assert "policy drop" in script
    assert "ct state established,related accept" not in script
    assert 'oif "lo" accept' in script
    assert "172.20.0.0/16" in script  # management plane (neo4j/daemon) stays reachable
    assert "10.0.0.0/24" in script  # in-scope target allowed
    assert "169.254.169.254" in script  # cloud metadata denied


def test_render_resolved_host_ips_added_to_allow_set():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["*.acme.com"]))
    script = render_nftables(pol, resolved_host_ips=["203.0.113.10"])
    assert "203.0.113.10" in script


def test_render_denies_precede_allows():
    pol = compile_egress_policy(
        _rules(mode="enforce", in_scope=["10.0.0.0/8"], out_of_scope=["10.1.2.0/24"])
    )
    script = render_nftables(pol)
    # Denylist precedence: an out-of-scope subnet inside an in-scope
    # supernet must be dropped, so the deny rule has to come first.
    assert script.index("10.1.2.0/24") < script.index("10.0.0.0/8")


def test_render_allows_dns_resolver_for_name_resolution():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    script = render_nftables(pol, resolver_addrs=["127.0.0.11"])
    assert "127.0.0.11" in script


def test_render_audit_mode_with_no_rules_is_valid_nonempty_script():
    pol = compile_egress_policy(_rules())
    script = render_nftables(pol)
    assert script.strip() != ""


# ── render_dns_allowlist ─────────────────────────────────────────────


def test_dns_allowlist_lists_in_scope_hosts_only():
    pol = compile_egress_policy(
        _rules(mode="enforce", in_scope=["*.acme.com", "prod.acme.com", "10.0.0.0/24"])
    )
    allow = set(render_dns_allowlist(pol))
    # In-scope hosts resolve (alongside the always-allowed OSINT search providers).
    assert {"*.acme.com", "prod.acme.com"} <= allow
    # CIDRs are an nft concern, not a DNS one.
    assert "10.0.0.0/24" not in allow


def test_dns_allowlist_empty_when_not_enforcing():
    pol = compile_egress_policy(_rules(mode="audit", in_scope=["*.acme.com"]))
    assert render_dns_allowlist(pol) == []


def test_egress_policy_is_frozen():
    pol = compile_egress_policy(_rules(mode="enforce", in_scope=["10.0.0.0/24"]))
    assert isinstance(pol, EgressPolicy)
    try:
        pol.enforce = False  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("EgressPolicy must be immutable")
