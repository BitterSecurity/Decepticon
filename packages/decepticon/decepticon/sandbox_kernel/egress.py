"""Sandbox-side RoE egress applier + nftables/DNS renderer.

This module lives in ``sandbox_kernel`` (not the framework) because it
runs *inside* the sandbox container, where ``nft`` is available, the
management subnet (sandbox-net, carrying Neo4j + the HTTP daemon) is
locally discoverable, and in-scope hostnames can be resolved to IPs. Per
the sandbox image's layering it must import only the standard library —
no ``decepticon_core``, no langchain.

The agent side (``decepticon.middleware.egress``) compiles the
engagement's ``machine_enforcement`` block into an :class:`EgressPolicy`
and ships its fields over the ``/provision_egress`` endpoint. The applier
here reconstructs the policy, folds in the locally-discovered management
plane + resolver + resolved host IPs, renders the nftables ruleset, and
loads it with ``nft -f``. One scope definition, two enforcement points.

Safety: the management plane is discovered *here* (from
``/proc/net/route`` — no external binary, as the OSS sandbox image ships
no ``ip``), never trusted from the wire — so a scope rule can never sever
the agent↔management link (Neo4j, the daemon, DNS). Enforcement is ON by
default for an enforcing policy; ``DECEPTICON_EGRESS_DISABLE`` is the
operator opt-out (passed as ``enabled=False`` by the provisioning hook /
endpoint) for an environment the in-sandbox firewall doesn't fit.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import struct
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

log = logging.getLogger("decepticon.sandbox_kernel.egress")

_NFT_TABLE = "inet decepticon_egress"

# Injection seam so the applier is unit-testable without a live ``nft``.
Runner = Callable[[Sequence[str], str], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """Network-boundary view of a ``MachineEnforcement`` scope.

    Plain stdlib dataclass (no ``decepticon_core`` dependency) so it can
    live in the sandbox image. ``enforce`` is set only in ENFORCE mode;
    ``default_drop`` only when an in-scope allowlist exists.
    """

    enforce: bool
    default_drop: bool
    allowed_cidrs: tuple[str, ...] = field(default_factory=tuple)
    allowed_hosts: tuple[str, ...] = field(default_factory=tuple)
    denied_cidrs: tuple[str, ...] = field(default_factory=tuple)
    denied_hosts: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "enforce": self.enforce,
            "default_drop": self.default_drop,
            "allowed_cidrs": list(self.allowed_cidrs),
            "allowed_hosts": list(self.allowed_hosts),
            "denied_cidrs": list(self.denied_cidrs),
            "denied_hosts": list(self.denied_hosts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> EgressPolicy:
        def _seq(key: str) -> tuple[str, ...]:
            raw = data.get(key) or []
            return tuple(str(x) for x in raw) if isinstance(raw, (list, tuple)) else ()

        return cls(
            enforce=bool(data.get("enforce", False)),
            default_drop=bool(data.get("default_drop", False)),
            allowed_cidrs=_seq("allowed_cidrs"),
            allowed_hosts=_seq("allowed_hosts"),
            denied_cidrs=_seq("denied_cidrs"),
            denied_hosts=_seq("denied_hosts"),
        )


@dataclass(frozen=True, slots=True)
class EgressApplyResult:
    applied: bool
    enforce: bool
    nft_ruleset: str
    dns_allowlist: tuple[str, ...]
    management_cidrs: tuple[str, ...]
    resolved_host_ips: tuple[str, ...]
    detail: str = ""


def _norm(addrs: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({a for a in addrs if a}))


def _split_family(addrs: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    v4: list[str] = []
    v6: list[str] = []
    for addr in addrs:
        try:
            net = ipaddress.ip_network(addr, strict=False)
        except ValueError:
            continue
        (v6 if net.version == 6 else v4).append(addr)
    return tuple(v4), tuple(v6)


def _daddr_rules(addrs: Iterable[str], verb: str) -> list[str]:
    """Emit ``ip``/``ip6 daddr { … } <verb>`` lines, split by family.

    An ``inet`` table needs the family-correct match (``ip`` vs ``ip6``);
    mixing an IPv6 literal into an ``ip daddr`` set is an nft load error.
    """
    v4, v6 = _split_family(addrs)
    lines: list[str] = []
    if v4:
        lines.append(f"    ip daddr {{ {', '.join(v4)} }} {verb}")
    if v6:
        lines.append(f"    ip6 daddr {{ {', '.join(v6)} }} {verb}")
    return lines


def render_nftables(
    policy: EgressPolicy,
    *,
    management_cidrs: Iterable[str] = (),
    resolver_addrs: Iterable[str] = (),
    resolved_host_ips: Iterable[str] = (),
    resolved_denied_host_ips: Iterable[str] = (),
) -> str:
    """Render an ``nft -f`` ruleset for ``policy``.

    The ruleset is idempotent: it destroys and recreates its own table,
    so re-provisioning (scope expansion) replaces rather than stacks
    rules and never touches other tables.

    Args:
        management_cidrs: subnets the applier discovered locally that must
            stay reachable (loopback, the sandbox-net carrying Neo4j + the
            HTTP daemon). Always accepted, ahead of any deny — a scope
            rule must never sever the agent↔management link.
        resolver_addrs: DNS resolver addresses (e.g. the Docker embedded
            resolver ``127.0.0.11``) so in-scope names still resolve.
        resolved_host_ips: IPs resolved from ``policy.allowed_hosts``,
            folded into the scope allow so connect-by-IP to an in-scope
            host is permitted.
        resolved_denied_host_ips: IPs resolved from literal denied hosts;
            these drop before broader CIDR and host allows.
    """
    policy_verb = "drop" if policy.default_drop else "accept"
    lines = [
        "#!/usr/sbin/nft -f",
        "# Decepticon RoE egress guardrail — generated; do not hand-edit.",
        # `add` then `delete` makes the destroy idempotent on first run
        # (delete of a missing table is an error; add-then-delete is not).
        f"add table {_NFT_TABLE}",
        f"delete table {_NFT_TABLE}",
        f"table {_NFT_TABLE} {{",
        "  chain output {",
        f"    type filter hook output priority 0; policy {policy_verb};",
    ]
    if policy.enforce:
        lines.append("    ct state established,related accept")
        lines.append('    oif "lo" accept')
        # Management plane + resolver: always reachable, before any deny.
        lines += _daddr_rules(_norm(management_cidrs), "accept")
        lines += _daddr_rules(_norm(resolver_addrs), "accept")
        # Denylist precedence: out-of-scope / forbidden ranges drop first,
        # so an out-of-scope subnet inside an in-scope supernet is blocked.
        lines += _daddr_rules(_norm((*policy.denied_cidrs, *resolved_denied_host_ips)), "drop")
        # Scope allowlist (literal CIDRs + applier-resolved host IPs).
        lines += _daddr_rules(_norm((*policy.allowed_cidrs, *resolved_host_ips)), "accept")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_dns_allowlist(policy: EgressPolicy) -> list[str]:
    """In-scope hostname / wildcard patterns the sandbox resolver may answer.

    Empty unless enforcing. CIDRs are an nftables concern, not a DNS one.
    """
    if not policy.enforce:
        return []
    return [
        host
        for host in policy.allowed_hosts
        if not any(fnmatchcase(host, denied) for denied in policy.denied_hosts)
    ]


def discover_management_cidrs(
    *,
    route_reader: Callable[[], str] | None = None,
    proc_route_path: str = "/proc/net/route",
) -> tuple[str, ...]:
    """Directly-attached subnets — the management plane to keep reachable.

    The sandbox sits on ``sandbox-net`` only; its on-link subnets carry
    Neo4j and the HTTP daemon, while targets are reached *through* the
    default gateway (off-link). Allowing egress to the on-link subnets
    keeps the management plane up without opening the internet.

    The **primary** source is ``/proc/net/route`` — always present on
    Linux and needs no external binary. (The OSS sandbox image ships no
    ``ip`` binary; relying on ``ip route`` alone discovered only loopback
    and would have dropped Neo4j/management traffic under default-drop —
    found in live verification.) ``ip -o route show`` is unioned in as a
    secondary source when the binary happens to exist. Loopback is added
    unconditionally; a discovery failure degrades to loopback only.
    """
    cidrs: set[str] = {"127.0.0.0/8"}
    cidrs |= _proc_route_onlink_cidrs(proc_route_path)
    reader = route_reader or _read_ip_routes
    try:
        out = reader()
    except Exception as exc:  # noqa: BLE001 - discovery must never raise into apply
        log.warning("egress: `ip route` discovery failed (using /proc only): %s", exc)
        out = ""
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        # On-link subnet routes look like: "172.20.0.0/16 dev eth0 proto
        # kernel scope link src 172.20.0.5". The first token is the CIDR.
        if "scope link" in line and "/" in parts[0]:
            try:
                ipaddress.ip_network(parts[0], strict=False)
            except ValueError:
                continue
            cidrs.add(parts[0])
    return _norm(cidrs)


def _proc_route_onlink_cidrs(proc_route_path: str) -> set[str]:
    """On-link IPv4 subnets from ``/proc/net/route`` (no external binary).

    Each row carries little-endian hex Destination / Gateway / Mask. A
    row with Gateway ``00000000`` is an on-link (directly-attached) subnet
    route; we convert Destination + Mask to a CIDR. The default route
    (Destination ``00000000``) has a non-zero Gateway and is skipped, so
    the internet/target path is never auto-allowed.
    """
    cidrs: set[str] = set()
    try:
        with open(proc_route_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        log.warning("egress: reading %s failed: %s", proc_route_path, exc)
        return cidrs
    for line in lines[1:]:  # skip header row
        parts = line.split()
        if len(parts) < 8:
            continue
        dest_hex, gw_hex, mask_hex = parts[1], parts[2], parts[7]
        if gw_hex != "00000000":  # has a gateway → off-link (default/route), skip
            continue
        dest = _le_hex_to_ipv4(dest_hex)
        mask = _le_hex_to_ipv4(mask_hex)
        if dest is None or mask is None:
            continue
        try:
            prefix = bin(int(ipaddress.IPv4Address(mask))).count("1")
            if prefix == 0:  # 0.0.0.0/0 — never auto-allow the whole internet
                continue
            net = ipaddress.ip_network(f"{dest}/{prefix}", strict=False)
        except ValueError:
            continue
        cidrs.add(str(net))
    return cidrs


def _le_hex_to_ipv4(hex_word: str) -> str | None:
    """Convert a little-endian 8-hex-char /proc word to a dotted-quad."""
    try:
        return socket.inet_ntoa(struct.pack("<L", int(hex_word, 16)))
    except (ValueError, OSError, struct.error):
        return None


def _read_ip_routes() -> str:
    return subprocess.run(
        ["ip", "-o", "route", "show"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    ).stdout


def discover_resolvers(*, resolv_conf: str = "/etc/resolv.conf") -> tuple[str, ...]:
    """Nameserver addresses from ``/etc/resolv.conf`` (Docker → 127.0.0.11)."""
    resolvers: set[str] = set()
    try:
        with open(resolv_conf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        resolvers.add(parts[1])
    except OSError as exc:
        log.warning("egress: reading %s failed: %s", resolv_conf, exc)
    return _norm(resolvers)


def resolve_hosts(
    hosts: Iterable[str],
    *,
    resolver: Callable[[str], list[str]] | None = None,
) -> tuple[str, ...]:
    """Best-effort resolve in-scope hostnames to IPs for the nft allowlist.

    Wildcards (``*.acme.com``) can't be pre-resolved — they are skipped
    here and enforced by the DNS allowlist; recon that discovers concrete
    hosts triggers a re-provision. Resolution failures are swallowed (the
    host simply isn't pre-authorised by IP yet).
    """
    resolve = resolver or _resolve_one
    ips: set[str] = set()
    for host in hosts:
        if not host or "*" in host or "?" in host:
            continue
        try:
            ips.update(resolve(host))
        except Exception as exc:  # noqa: BLE001 - resolution is best-effort
            log.debug("egress: resolve %s failed: %s", host, exc)
    return _norm(ips)


def _resolve_one(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    return [str(info[4][0]) for info in infos]


def apply_egress(
    policy: EgressPolicy,
    *,
    enabled: bool,
    runner: Runner | None = None,
    management_cidrs: Iterable[str] | None = None,
    resolver_addrs: Iterable[str] | None = None,
    host_resolver: Callable[[Iterable[str]], tuple[str, ...]] | None = None,
) -> EgressApplyResult:
    """Compile + load the egress ruleset inside the sandbox.

    When ``enabled`` is False (the operator opted out via
    ``DECEPTICON_EGRESS_DISABLE``), nothing touches the network — the
    rendered ruleset is returned for inspection but never loaded, so the
    management plane is guaranteed intact.

    Returns an :class:`EgressApplyResult` describing what was (or would
    have been) applied; never raises into the caller's hot path.
    """
    mgmt = tuple(management_cidrs) if management_cidrs is not None else discover_management_cidrs()
    resolvers = tuple(resolver_addrs) if resolver_addrs is not None else discover_resolvers()
    resolve = host_resolver or resolve_hosts
    allowed_hosts = render_dns_allowlist(policy)
    resolved = resolve(allowed_hosts) if policy.enforce and allowed_hosts else ()
    denied = resolve(policy.denied_hosts) if policy.enforce and policy.denied_hosts else ()

    ruleset = render_nftables(
        policy,
        management_cidrs=mgmt,
        resolver_addrs=resolvers,
        resolved_host_ips=resolved,
        resolved_denied_host_ips=denied,
    )
    dns = tuple(render_dns_allowlist(policy))

    if not enabled or not policy.enforce:
        return EgressApplyResult(
            applied=False,
            enforce=policy.enforce,
            nft_ruleset=ruleset,
            dns_allowlist=dns,
            management_cidrs=_norm(mgmt),
            resolved_host_ips=tuple(resolved),
            detail="egress enforcement disabled" if not enabled else "audit/warn mode",
        )

    run = runner or _run_nft
    try:
        proc = run(["nft", "-f", "-"], ruleset)
    except Exception as exc:  # noqa: BLE001 - never break the engagement on apply failure
        log.error("egress: nft load raised: %s", exc)
        return EgressApplyResult(
            applied=False,
            enforce=True,
            nft_ruleset=ruleset,
            dns_allowlist=dns,
            management_cidrs=_norm(mgmt),
            resolved_host_ips=tuple(resolved),
            detail=f"nft load raised: {exc}",
        )
    if proc.returncode != 0:
        log.error("egress: nft load failed rc=%s: %s", proc.returncode, proc.stderr)
        return EgressApplyResult(
            applied=False,
            enforce=True,
            nft_ruleset=ruleset,
            dns_allowlist=dns,
            management_cidrs=_norm(mgmt),
            resolved_host_ips=tuple(resolved),
            detail=f"nft load failed rc={proc.returncode}: {proc.stderr.strip()}",
        )
    return EgressApplyResult(
        applied=True,
        enforce=True,
        nft_ruleset=ruleset,
        dns_allowlist=dns,
        management_cidrs=_norm(mgmt),
        resolved_host_ips=tuple(resolved),
        detail="nft ruleset loaded",
    )


def _run_nft(argv: Sequence[str], stdin: str) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(argv),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


__all__ = [
    "EgressApplyResult",
    "EgressPolicy",
    "apply_egress",
    "discover_management_cidrs",
    "discover_resolvers",
    "render_dns_allowlist",
    "render_nftables",
    "resolve_hosts",
]
