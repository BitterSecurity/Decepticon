"""Tests for the sandbox-side egress applier.

The applier runs inside the sandbox container. These tests inject the
``nft`` runner and the route/resolver readers so the rendering, the
management-plane discovery, and the disabled/enabled gating are all
exercised without a live ``nft`` or a real network.
"""

from __future__ import annotations

import os

from decepticon.sandbox_kernel.egress import (
    EgressPolicy,
    apply_egress,
    discover_management_cidrs,
    discover_resolvers,
    render_nftables,
    resolve_hosts,
)

# A real ``/proc/net/route`` (little-endian hex): a default route via a
# gateway (skipped) + the on-link 172.19.0.0/16 subnet (kept). This is the
# exact shape seen in the live Kali sandbox, which ships no ``ip`` binary.
_PROC_ROUTE = (
    "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
    "eth0\t00000000\t010013AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    "eth0\t000013AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n"
)


def _enforce_policy(**kw) -> EgressPolicy:
    base = dict(enforce=True, default_drop=True, allowed_cidrs=("10.0.0.0/24",))
    base.update(kw)
    return EgressPolicy(**base)  # type: ignore[arg-type]


# ── discovery ────────────────────────────────────────────────────────


def test_discover_management_cidrs_from_ip_route_onlink(tmp_path):
    routes = (
        "default via 172.20.0.1 dev eth0\n"
        "172.20.0.0/16 dev eth0 proto kernel scope link src 172.20.0.5\n"
    )
    # Empty /proc source so only the `ip route` path is under test here.
    empty_proc = tmp_path / "route"
    empty_proc.write_text("Iface\tDestination\tGateway\n")
    cidrs = discover_management_cidrs(route_reader=lambda: routes, proc_route_path=str(empty_proc))
    assert "172.20.0.0/16" in cidrs  # sandbox-net (neo4j + daemon) stays reachable
    assert "127.0.0.0/8" in cidrs
    # The default route's gateway (off-link → internet/targets) is NOT a
    # management subnet and must not be auto-allowed.
    assert "default" not in " ".join(cidrs)


def test_discover_management_cidrs_from_proc_route_no_ip_binary(tmp_path):
    """The OSS sandbox image ships no ``ip`` binary — the management subnet
    MUST still be discovered from /proc/net/route, or default-drop would
    sever Neo4j/management traffic (found in live verification)."""
    proc = tmp_path / "route"
    proc.write_text(_PROC_ROUTE)

    def no_ip() -> str:
        raise FileNotFoundError("No such file or directory: 'ip'")

    cidrs = discover_management_cidrs(route_reader=no_ip, proc_route_path=str(proc))
    assert "172.19.0.0/16" in cidrs  # on-link subnet recovered without `ip`
    assert "127.0.0.0/8" in cidrs


def test_discover_management_cidrs_survives_total_failure(tmp_path):
    def boom() -> str:
        raise OSError("no ip command")

    cidrs = discover_management_cidrs(route_reader=boom, proc_route_path=str(tmp_path / "missing"))
    assert cidrs == ("127.0.0.0/8",)


def test_discover_management_cidrs_default_proc_path_is_readable():
    # Smoke: the real /proc/net/route parses without raising on this host.
    assert os.path.exists("/proc/net/route")
    cidrs = discover_management_cidrs(route_reader=lambda: "")
    assert "127.0.0.0/8" in cidrs


def test_discover_resolvers_reads_nameservers(tmp_path):
    rc = tmp_path / "resolv.conf"
    rc.write_text("search lan\nnameserver 127.0.0.11\nnameserver 8.8.8.8\n")
    resolvers = discover_resolvers(resolv_conf=str(rc))
    assert "127.0.0.11" in resolvers
    assert "8.8.8.8" in resolvers


def test_resolve_hosts_skips_wildcards_and_resolves_literals():
    resolved = resolve_hosts(
        ["*.acme.com", "prod.acme.com"],
        resolver=lambda h: ["203.0.113.9"] if h == "prod.acme.com" else [],
    )
    assert "203.0.113.9" in resolved


# ── apply gating ─────────────────────────────────────────────────────


def test_apply_disabled_never_runs_nft():
    calls: list = []

    def runner(argv, stdin):  # pragma: no cover - must not be called
        calls.append(argv)
        raise AssertionError("nft must not run when disabled")

    result = apply_egress(
        _enforce_policy(),
        enabled=False,
        runner=runner,
        management_cidrs=["172.20.0.0/16"],
        resolver_addrs=["127.0.0.11"],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is False
    assert calls == []
    # Even disabled, it renders the ruleset for inspection.
    assert "policy drop" in result.nft_ruleset


def test_apply_audit_policy_does_not_run_nft_even_when_enabled():
    calls: list = []

    def runner(argv, stdin):  # pragma: no cover
        calls.append(argv)
        raise AssertionError("nft must not run for a non-enforcing policy")

    audit = EgressPolicy(enforce=False, default_drop=False)
    result = apply_egress(
        audit,
        enabled=True,
        runner=runner,
        management_cidrs=[],
        resolver_addrs=[],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is False
    assert calls == []


class _OkProc:
    returncode = 0
    stderr = ""
    stdout = ""


def test_apply_enabled_loads_ruleset_via_nft():
    seen = {}

    def runner(argv, stdin):
        seen["argv"] = list(argv)
        seen["stdin"] = stdin
        return _OkProc()

    result = apply_egress(
        _enforce_policy(),
        enabled=True,
        runner=runner,
        management_cidrs=["172.20.0.0/16"],
        resolver_addrs=["127.0.0.11"],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is True
    assert seen["argv"] == ["nft", "-f", "-"]
    assert "172.20.0.0/16" in seen["stdin"]  # management injected by applier
    assert "10.0.0.0/24" in seen["stdin"]
    assert "127.0.0.11" in seen["stdin"]


class _FailProc:
    returncode = 1
    stderr = "syntax error"
    stdout = ""


def test_apply_reports_nft_failure_without_raising():
    result = apply_egress(
        _enforce_policy(),
        enabled=True,
        runner=lambda argv, stdin: _FailProc(),
        management_cidrs=["172.20.0.0/16"],
        resolver_addrs=[],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is False
    assert "rc=1" in result.detail


def test_apply_resolves_allowed_hosts_into_nft_allow():
    host_policy = EgressPolicy(enforce=True, default_drop=True, allowed_hosts=("prod.acme.com",))
    seen = {}

    def runner(argv, stdin):
        seen["stdin"] = stdin
        return _OkProc()

    result = apply_egress(
        host_policy,
        enabled=True,
        runner=runner,
        management_cidrs=["172.20.0.0/16"],
        resolver_addrs=["127.0.0.11"],
        host_resolver=lambda hosts: ("203.0.113.20",),
    )
    assert result.applied is True
    assert "203.0.113.20" in seen["stdin"]


def test_denied_host_wins_over_allowed_host_and_cidr():
    policy = EgressPolicy(
        enforce=True,
        default_drop=True,
        allowed_hosts=("victim.example",),
        allowed_cidrs=("203.0.113.0/24",),
        denied_hosts=("victim.example",),
    )
    resolved_for: list[tuple[str, ...]] = []

    def resolver(hosts):
        resolved_for.append(tuple(hosts))
        return ("203.0.113.20",)

    result = apply_egress(
        policy,
        enabled=True,
        runner=lambda argv, stdin: _OkProc(),
        management_cidrs=[],
        resolver_addrs=[],
        host_resolver=resolver,
    )
    assert result.applied is True
    assert result.dns_allowlist == ()
    assert resolved_for == [("victim.example",)]
    assert result.nft_ruleset.index("203.0.113.20 } drop") < result.nft_ruleset.index(
        "203.0.113.0/24 } accept"
    )


def test_wildcard_denied_host_fails_closed_without_dns_firewall():
    policy = EgressPolicy(
        enforce=True,
        default_drop=False,
        allowed_cidrs=("203.0.113.0/24",),
        denied_hosts=("*.blocked.example",),
    )
    result = apply_egress(
        policy,
        enabled=True,
        runner=lambda argv, stdin: _OkProc(),
        management_cidrs=[],
        resolver_addrs=[],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is True
    assert "policy drop" in result.nft_ruleset
    assert "203.0.113.0/24 } accept" not in result.nft_ruleset


def test_unresolved_denied_host_fails_closed():
    policy = EgressPolicy(
        enforce=True,
        default_drop=True,
        allowed_cidrs=("203.0.113.0/24",),
        denied_hosts=("blocked.example",),
    )
    result = apply_egress(
        policy,
        enabled=True,
        runner=lambda argv, stdin: _OkProc(),
        management_cidrs=[],
        resolver_addrs=[],
        host_resolver=lambda hosts: (),
    )
    assert result.applied is True
    assert "203.0.113.0/24 } accept" not in result.nft_ruleset


def test_render_matches_apply_disabled_ruleset():
    pol = _enforce_policy()
    direct = render_nftables(pol, management_cidrs=["172.20.0.0/16"], resolver_addrs=["127.0.0.11"])
    via_apply = apply_egress(
        pol,
        enabled=False,
        management_cidrs=["172.20.0.0/16"],
        resolver_addrs=["127.0.0.11"],
        host_resolver=lambda hosts: (),
    ).nft_ruleset
    assert direct == via_apply
