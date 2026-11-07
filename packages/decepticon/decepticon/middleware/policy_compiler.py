"""Compile a machine RoE block into an OpenShell sandbox policy.

This is the OpenShell half of the Layer-2 egress guardrail. It turns the
**same** ``roe.json:machine_enforcement`` block the parser reads
(:mod:`decepticon.middleware.roe`) into a kernel-enforced OpenShell
``network_policies`` mapping, so the sandbox refuses out-of-scope network
contact even when the command parser never sees the target. See
``docs/design/2026-11-03-roe-openshell-boundary.md`` for the boundary map.

The compilation is deterministic: the same machine RoE always renders the
same policy mapping and the same :attr:`CompiledRoePolicy.canonical_hash`,
which is how a provisioned sandbox proves that its installed policy
matches the engagement's RoE. The translation has no inference in it.

Fail-closed rules — compilation refuses (returns non-empty ``errors``)
whenever the kernel policy would be *wider* than the parser:

* an in-scope rule's match set covers a forbidden destination, a
  sensitive-TLD host, or an out-of-scope entry (the parser denies those
  hosts; a kernel allow rule must not admit them);
* an in-scope glob is not a leading single-label ``*.`` wildcard — the
  OpenShell host matcher's proven form (``matches_domain_glob`` is
  single-label, and non-leading globs are not kernel-proven);
* the RoE lists no in-scope entries (a deny-all network is legal but
  must be an explicit operator decision, mirroring
  ``build_engagement_roe``).

Ports and binaries are narrowed contracts, not approximations: the
legacy nftables path enforced hosts for *any* port and *any* process,
while an OpenShell endpoint pins ports and a rule pins binaries. Defaults
keep parity (``binaries: [{path: "**"}]`` permits any process) and operators narrow
them with ``DECEPTICON_OPENSHELL_ROE_PORTS`` /
``DECEPTICON_OPENSHELL_ROE_BINARIES``.

Kernel invariants carry part of the RoE by themselves: loopback,
link-local (``169.254.0.0/16``) and cloud-metadata destinations are
always refused upstream regardless of rules, which is why
``forbidden_destinations`` never renders as allow entries.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from decepticon.middleware.egress import osint_egress_hosts
from decepticon_core.types.roe import (
    MachineEnforcement,
    ScopeRule,
    evaluate_target,
    matches_domain_glob,
    network_scope_pattern,
)

# Common web ports for pinned endpoint contracts. The parser is
# port-agnostic on hosts; the kernel endpoint pins ports, so this list is
# the documented residual narrowing. Extend via
# DECEPTICON_OPENSHELL_ROE_PORTS (comma-separated ints).
_DEFAULT_ROE_PORTS = ("80", "443", "8000", "8080", "8443")


@dataclass(frozen=True)
class CompiledRoePolicy:
    """A deterministic OpenShell policy compiled from a machine RoE.

    ``policy`` uses the OpenShell policy YAML key vocabulary
    (``filesystem_policy``/``network_policies``) so it can be handed to
    the gateway CLI or converted to the ``SandboxPolicy`` proto by the
    trusted resolver. ``canonical_hash`` is the sha256 over canonical
    JSON bytes of ``policy`` — the value admission binds to
    ``effective_policy_hash``. ``errors`` non-empty means refuse: the
    policy must not be installed.
    """

    policy: dict
    canonical_hash: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def roe_ports() -> tuple[int, ...]:
    """Ports pinned on every compiled host/CIDR endpoint (sorted, deduped)."""
    raw = os.environ.get("DECEPTICON_OPENSHELL_ROE_PORTS")
    tokens = raw.split(",") if raw is not None else _DEFAULT_ROE_PORTS
    ports: set[int] = set()
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        try:
            port = int(token)
        except ValueError as exc:
            raise ValueError(f"DECEPTICON_OPENSHELL_ROE_PORTS: invalid port {token!r}") from exc
        if not 1 <= port <= 65535:
            raise ValueError(f"DECEPTICON_OPENSHELL_ROE_PORTS: port {port} out of range")
        ports.add(port)
    return tuple(sorted(ports))


ANY_BINARY_GLOB = "**"


def roe_binaries() -> tuple[str, ...]:
    """Binary paths scoped on compiled rules.

    Defaults to the ``**`` glob (every process), matching the retired
    nftables path where every process was subject to the host allowlist.
    Verified live: an omitted or empty ``binaries`` field is NOT
    "any process" on the 0.1.6 runtime — it permits nothing (every
    connection is refused with "binary not allowed"), so the parity glob
    must be explicit. Set ``DECEPTICON_OPENSHELL_ROE_BINARIES`` to a
    comma-separated list of absolute paths (globs allowed) to adopt the
    kernel's binary×host scoping.
    """
    raw = os.environ.get("DECEPTICON_OPENSHELL_ROE_BINARIES")
    if raw is None:
        return (ANY_BINARY_GLOB,)
    return tuple(sorted({p.strip() for p in raw.split(",") if p.strip()}))


def _sensitive_tlds(rules: MachineEnforcement) -> tuple[str, ...]:
    return () if rules.allow_sensitive_tlds else ("gov", "mil", "edu", "int")


def _rule_covers_host(kind: str, pattern: str, host: str) -> bool:
    """Whether one in-scope rule's match set contains ``host`` (kernel view).

    Mirrors the parser matcher semantics without private core helpers:
    CIDR membership, the public ``matches_domain_glob`` for globs, and
    case-insensitive equality for exact hosts.
    """
    target = host.rstrip(".").lower()
    if kind == "cidr":
        try:
            return ipaddress.ip_address(target) in ipaddress.ip_network(pattern, strict=False)
        except ValueError:
            return False
    if kind == "domain-glob":
        return matches_domain_glob(pattern.rstrip("."), target)
    return pattern.rstrip(".").lower() == target


def _rule_ports(rule: ScopeRule) -> set[int]:
    """Ports an in-scope URL rule explicitly authorizes beyond the defaults."""
    pattern = rule.pattern
    if not pattern.lower().startswith(("http://", "https://")):
        return set()
    try:
        parsed = urlsplit(pattern)
    except ValueError:
        return set()
    if parsed.port is None:
        return set()
    return {parsed.port}


def _in_scope_sections(
    rules: MachineEnforcement,
    errors: list[str],
) -> tuple[list[dict], list[dict]]:
    """Render in-scope rules into (host endpoints, hostless CIDR endpoints)."""
    host_endpoints: list[dict] = []
    cidr_endpoints: list[dict] = []
    ports = list(roe_ports())
    for rule in rules.in_scope:
        ports.extend(_rule_ports(rule))
    ports = sorted(set(ports))

    for rule in rules.in_scope:
        pattern = network_scope_pattern(rule)
        if pattern is None:
            errors.append(
                f"in_scope {rule.pattern!r}: URL form cannot become a network rule "
                "(non-default port, credentials, path/query/fragment)"
            )
            continue
        kind = rule.resolved_kind() if pattern == rule.pattern else "host"
        if kind in ("host", "domain-glob"):
            if kind == "domain-glob" and not (
                pattern.startswith("*.") and "*" not in pattern[2:] and "?" not in pattern[2:]
            ):
                errors.append(
                    f"in_scope glob {pattern!r}: only leading single-label '*.base' "
                    "wildcards are kernel-proven; enumerate hosts or use '*.base'"
                )
                continue
            host_endpoints.append(
                {
                    "host": pattern.rstrip(".").lower(),
                    "ports": ports,
                    "protocol": "rest",
                    "enforcement": "enforce",
                    "access": "full",
                }
            )
        elif kind in ("cidr", "ip"):
            # Hostless allowlist rides the forward proxy; protocol: tcp
            # requires a DNS hostname and cannot represent IP scopes. The
            # endpoint still declares enforce/full metadata: hostless rules
            # overlap the rest endpoints' ports (they match any domain),
            # and the gateway validator rejects ambiguous overlaps with
            # conflicting enforcement metadata.
            cidr_endpoints.append(
                {
                    "ports": ports,
                    "allowed_ips": [pattern],
                    "enforcement": "enforce",
                    "access": "full",
                }
            )
        else:  # pragma: no cover - resolved_kind is closed over these values
            errors.append(f"in_scope {pattern!r}: unknown rule kind {kind!r}")
    return host_endpoints, cidr_endpoints


def _rule_identity(rule: ScopeRule, *, deny: bool = False) -> tuple[str, str] | None:
    pattern = network_scope_pattern(rule, deny=deny)
    if pattern is None:
        return None
    kind = rule.resolved_kind() if pattern == rule.pattern else "host"
    return kind, pattern.rstrip(".").lower()


def _intersects(allow: tuple[str, str], deny: tuple[str, str]) -> bool:
    """Prove disjointness for compiled host/leading-wildcard/IP scope sets.

    Deny globs retain the parser's arbitrary single-label wildcard grammar.
    Unknown IP/glob intersections refuse conservatively rather than sampling
    a pattern string as if it were a concrete destination.
    """
    ak, ap = allow
    dk, dp = deny
    if ak in ("ip", "cidr"):
        network = ipaddress.ip_network(ap, strict=False)
        if dk in ("ip", "cidr"):
            other = ipaddress.ip_network(dp, strict=False)
            return network.version == other.version and network.overlaps(other)
        if dk == "domain-glob":
            alphabet = "0123456789.*?" if network.version == 4 else "0123456789abcdef:*?"
            return all(char in alphabet for char in dp)
        try:
            return ipaddress.ip_address(dp) in network
        except ValueError:
            return False
    if ak == "host":
        return _rule_covers_host(dk, dp, ap)
    if ak == "domain-glob":
        if dk in ("ip", "cidr"):
            # A numeric DNS wildcard can also match IP-literal targets.
            alphabet = (
                "0123456789.*?"
                if ipaddress.ip_network(dp, strict=False).version == 4
                else "0123456789abcdef:*?"
            )
            return all(char in alphabet for char in ap)
        if dk == "host":
            return _rule_covers_host(ak, ap, dp)
        allowed_labels, denied_labels = ap.split("."), dp.split(".")
        if len(allowed_labels) != len(denied_labels):
            return False
        # The compiler admits only a leading '*' label. All remaining labels
        # are concrete, so checking them against each denied label is exact.
        return bool(denied_labels[0]) and all(
            matches_domain_glob(pattern, host)
            for pattern, host in zip(denied_labels[1:], allowed_labels[1:])
        )
    return True  # unknown grammar cannot establish a safe kernel superset


def _coverage_errors(rules: MachineEnforcement, errors: list[str]) -> None:
    """Reject nonempty intersections with parser-denied destination sets."""
    denied = [
        (f"forbidden {dest}", ScopeRule(pattern=dest), False)
        for dest in rules.effective_forbidden_destinations()
    ]
    denied.extend((f"out_of_scope {rule.pattern}", rule, True) for rule in rules.out_of_scope)
    for rule in rules.in_scope:
        allow = _rule_identity(rule)
        if allow is None:
            continue
        kind, pattern = allow
        if kind in ("host", "domain-glob"):
            for tld in _sensitive_tlds(rules):
                if pattern == tld or pattern.endswith("." + tld):
                    errors.append(f"in_scope {pattern!r} kernel-covers sensitive TLD .{tld}")
        for label, denied_rule, is_deny in denied:
            deny = _rule_identity(denied_rule, deny=is_deny)
            if deny is not None and _intersects(allow, deny):
                errors.append(
                    f"in_scope {pattern!r} kernel-covers {label}; "
                    "narrow the scope so the kernel is never wider than the parser"
                )


def compile_openshell_policy(rules: MachineEnforcement) -> CompiledRoePolicy:
    """Compile a machine RoE into the OpenShell sandbox policy mapping.

    Returns a :class:`CompiledRoePolicy`; callers must treat non-empty
    ``errors`` as fail-closed and never install the policy.
    """
    errors: list[str] = []
    if not rules.in_scope:
        errors.append(
            "in_scope is empty: a compiled kernel policy would deny all network "
            "egress; scope entries are required (explicit operator decision)"
        )
    _coverage_errors(rules, errors)
    host_endpoints, cidr_endpoints = _in_scope_sections(rules, errors)
    if errors:
        return CompiledRoePolicy(policy={}, canonical_hash="", errors=tuple(errors))

    binaries = [{"path": path} for path in roe_binaries()]
    rule: dict = {
        "name": "roe-in-scope",
        "endpoints": sorted(
            host_endpoints + cidr_endpoints,
            key=lambda ep: (ep.get("host") or ",".join(ep.get("allowed_ips", ())),),
        ),
    }
    rule["binaries"] = binaries
    policy: dict = {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": True,
            # Sandbox runtime invariant: the tmux-based exec substrate needs
            # PTY allocation, which the default-deny filesystem policy blocks
            # at the seccomp-notify layer (verified live: /dev/ptmx and
            # /dev/tty EPERM without these; "create window failed: fork
            # failed: Permission denied" with them absent).
            "read_write": ["/dev/ptmx", "/dev/tty", "/dev/pts"],
        },
        "network_policies": {
            "roe_in_scope": rule,
            "osint_allowance": {
                "name": "osint-allowance",
                "endpoints": [
                    {
                        "host": host,
                        "ports": list(roe_ports()),
                        "protocol": "rest",
                        "enforcement": "enforce",
                        "access": "full",
                    }
                    for host in sorted(osint_egress_hosts())
                    if evaluate_target(host, replace(rules, in_scope=())).allow
                ],
                **({"binaries": binaries} if binaries else {}),
            },
        },
    }
    canonical = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(canonical).hexdigest()
    return CompiledRoePolicy(policy=policy, canonical_hash=digest, errors=())


def sandbox_binding_digest(workspace_path: str, roe_hash: str, *, approval_hash: str) -> str:
    """Full SHA256 binding in label-safe base32; never truncate owner identity."""
    for digest in (roe_hash, approval_hash):
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("Full canonical policy and approval SHA256 required")
    identity = {
        "version": 1,
        "workspace": str(Path(workspace_path).resolve()),
        "policy": roe_hash,
        "approval": approval_hash,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return base64.b32encode(hashlib.sha256(canonical).digest()).decode().rstrip("=").lower()


def sandbox_name(workspace_path: str, roe_hash: str, *, approval_hash: str | None = None) -> str:
    """19-character locator, hashing the complete workspace/policy pair.

    The 70-bit locator is not authority. Admission must also verify the full
    256-bit binding label before and after reading policy/ACK. A locator
    collision fails closed; legacy names and unlabeled runtimes are retained,
    never adopted, renamed or deleted by this function.
    """
    return (
        "dcpt-"
        + sandbox_binding_digest(
            workspace_path,
            roe_hash,
            approval_hash=approval_hash if approval_hash is not None else roe_hash,
        )[:14]
    )
