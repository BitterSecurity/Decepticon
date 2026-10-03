"""Network-isolation invariants for ``docker-compose.yml``.

These assertions encode the structural security boundaries that no
single Semgrep / lint rule can catch. They run on the *rendered* compose
file (``docker compose config``) so they validate the post-interpolation
shape that actually starts on a contributor's host.

Invariants
----------

1. **Operational services never join the management network.**
   The Kali sandbox, the C2 framework, and the optional Ghidra MCP
   sidecar are sandbox-net only. Adding any of them to
   ``decepticon-net`` collapses the management/operational boundary
   that the architecture is built around (see
   ``docs/security/sandbox-isolation.md``).

2. **No service uses host networking.** ``network_mode: host`` bypasses
   both bridges and defeats the isolation entirely.

3. **No service mounts the Docker socket.** The HTTP-only sandbox
   migration removed ``/var/run/docker.sock`` from the LangGraph
   service deliberately; reintroducing it would re-open a
   container-escape path from a compromised orchestrator.

4. **Every published port binds to 127.0.0.1.** Bare ``PORT:PORT``
   (which docker resolves to ``0.0.0.0``) exposes internal services to
   the contributor's LAN. The existing compose already follows this
   convention; the test fences it.

5. **Dual-homed services are explicitly allowlisted.** Today
   ``neo4j`` and ``langgraph`` are intentionally on both networks.
   Adding a third forces an explicit update to ``DUAL_HOMED_SERVICES``
   below, which is what we want — silent additions are how an
   isolation boundary erodes.

If you are extending the compose file and one of these assertions
fires, the message points at the invariant being violated and what to
update. **Do not** weaken the test to make a change land; either fix
the compose, or open an ADR that supersedes the invariant.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "docker-compose.yml"

MGMT_NET = "decepticon-net"
SANDBOX_NET = "sandbox-net"

# Services that MUST NOT touch the management network under any
# circumstance. These are the services that execute against external
# targets or expose adversary-tooling interfaces.
OPERATIONAL_ONLY: frozenset[str] = frozenset(
    {
        "sandbox",
        "c2-sliver",
        "ghidra-mcp",
    }
)

# The only services permitted on BOTH networks. Adding a third
# requires an ADR + a deliberate update here.
DUAL_HOMED_SERVICES: frozenset[str] = frozenset(
    {
        "neo4j",
        "langgraph",
    }
)

# The only services permitted to mount the host docker socket. `cli` needs
# it for the /web slash command, which shells out to
# `docker compose --profile web up -d web` against the host daemon (#625).
# It is a client, not an agent: it executes operator keystrokes, never
# model-directed commands, so it is not the container-escape surface this
# guard exists for. Multi-tenant deployments drop the mount via a compose
# overlay. Adding a service that *does* run agent code here is not a
# deliberate update — it is the regression this test catches.
DOCKER_SOCKET_SERVICES: frozenset[str] = frozenset(
    {
        "cli",
    }
)


@pytest.fixture(autouse=True)
def _ensure_dotenv():
    """``docker compose config`` needs a .env to satisfy env_file refs."""
    env_file = REPO_ROOT / ".env"
    created = False
    if not env_file.exists():
        env_file.write_text("")
        created = True
    yield
    if created:
        env_file.unlink(missing_ok=True)


def _rendered_compose() -> dict:
    """Return the post-interpolation compose document as a dict.

    Uses ``--profiles`` so that profile-gated services
    (c2-sliver, ghidra-mcp, cli) are present in the render and
    therefore subject to the same invariants as the always-on
    services.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not available")
    env = {
        **os.environ,
        # COMPOSE_PROFILES enables every profile in the file so the
        # render sees every service. The list mirrors the profiles
        # defined in docker-compose.yml; extend here when a new
        # profile is added.
        "COMPOSE_PROFILES": "c2-sliver,reversing,cli",
    }
    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config"],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            "docker compose config failed (exit "
            f"{result.returncode}):\n"
            f"--- stderr ---\n{result.stderr}\n"
            f"--- stdout ---\n{result.stdout}"
        )
    rendered = yaml.safe_load(result.stdout)
    if not isinstance(rendered, dict) or "services" not in rendered:
        pytest.fail(f"rendered compose has no `services` key:\n{result.stdout[:500]}")
    return rendered


def _service_networks(service: dict) -> set[str]:
    """Return the set of network names a service is attached to.

    Compose ``networks:`` may be a list (``[a, b]``) or a mapping
    (``{a: null, b: {aliases: ...}}``). Both shapes mean
    membership.
    """
    nets = service.get("networks")
    if nets is None:
        return set()
    if isinstance(nets, list):
        return set(nets)
    if isinstance(nets, dict):
        return set(nets.keys())
    return set()


def _service_volumes(service: dict) -> list[str]:
    """Return source paths of all volume mounts as plain strings."""
    raw = service.get("volumes") or []
    out: list[str] = []
    for v in raw:
        if isinstance(v, str):
            out.append(v.split(":", 1)[0])
        elif isinstance(v, dict):
            src = v.get("source")
            if src:
                out.append(str(src))
    return out


def test_operational_services_never_on_management_network():
    """sandbox, c2-sliver, ghidra-mcp must never appear on decepticon-net.

    A regression here collapses the management/operational boundary —
    a compromised target-facing container would gain a path to LiteLLM
    keys, the Postgres engagement log, and the Web dashboard.
    """
    services = _rendered_compose()["services"]
    violations: list[str] = []
    for name in OPERATIONAL_ONLY:
        svc = services.get(name)
        if svc is None:
            # The service is conditional (profile-gated); if it is
            # absent we cannot validate it. The render above enables
            # every known profile, so absence here means the service
            # was removed entirely — record so an intentional removal
            # forces a test update too.
            continue
        nets = _service_networks(svc)
        if MGMT_NET in nets:
            violations.append(f"service '{name}' is on '{MGMT_NET}' but must be sandbox-only")
    assert not violations, (
        "operational/management network boundary violated:\n  "
        + "\n  ".join(violations)
        + "\n\nFix: remove the management-network membership, or open an "
        "ADR that supersedes docs/security/sandbox-isolation.md."
    )


def test_no_service_uses_host_networking():
    """``network_mode: host`` bypasses both bridges entirely."""
    services = _rendered_compose()["services"]
    violations = [name for name, svc in services.items() if svc.get("network_mode") == "host"]
    assert not violations, (
        "services with network_mode: host (forbidden): "
        f"{sorted(violations)}\n"
        "Fix: attach the service to an explicit bridge network."
    )


def test_only_allowlisted_services_mount_docker_socket():
    """``/var/run/docker.sock`` is confined to DOCKER_SOCKET_SERVICES.

    The HTTP-only sandbox migration replaced ``docker exec`` with a
    FastAPI daemon on port 9999 inside the sandbox container. Mounting
    the docker socket into a service that runs agent code reintroduces a
    container-escape path — a compromised process there can spawn or
    modify peer containers, including the management plane.
    """
    services = _rendered_compose()["services"]
    actual: set[str] = set()
    for name, svc in services.items():
        if any("docker.sock" in src for src in _service_volumes(svc)):
            actual.add(name)
    unexpected = actual - DOCKER_SOCKET_SERVICES
    stale = DOCKER_SOCKET_SERVICES - actual
    msgs: list[str] = []
    if unexpected:
        msgs.append(
            f"services mounting docker.sock without an allowlist entry: {sorted(unexpected)}\n"
            "Fix: use the sandbox HTTP daemon (port 9999) instead of docker exec "
            "(see packages/decepticon/decepticon/backends/), or — if the service runs "
            "no agent code — add it to DOCKER_SOCKET_SERVICES with the reason."
        )
    if stale:
        msgs.append(
            f"DOCKER_SOCKET_SERVICES lists services that no longer mount it: {sorted(stale)}\n"
            "Fix: drop the stale entry so the allowlist stays honest."
        )
    assert not msgs, "\n\n".join(msgs)


def test_cli_exposes_host_install_path_separately_from_container_path(monkeypatch):
    monkeypatch.setenv("DECEPTICON_HOME", "/tmp/decepticon-operator-home")
    environment = _rendered_compose()["services"]["cli"]["environment"]
    assert environment["DECEPTICON_HOME"] == "/decepticon-home"
    assert environment["DECEPTICON_HOST_HOME"] == "/tmp/decepticon-operator-home"


def test_published_ports_bind_to_loopback():
    """Every published port must bind to 127.0.0.1, not 0.0.0.0.

    A bare ``"PORT:PORT"`` resolves to ``0.0.0.0:PORT`` on the host,
    exposing the service to every machine on the contributor's LAN.
    For a developer laptop on a hotel / coffee-shop / coworking
    network, this is an immediate exposure of LiteLLM keys, the Neo4j
    knowledge graph, and any sandbox state.
    """
    services = _rendered_compose()["services"]
    violations: list[str] = []
    for name, svc in services.items():
        for entry in svc.get("ports") or []:
            host_ip: str | None = None
            published: str | None = None
            if isinstance(entry, str):
                # Short form rendered by ``compose config`` is usually
                # already expanded to ``IP:PUBLISHED:TARGET``. Split
                # on ':' from the right: TARGET is the rightmost
                # field, PUBLISHED next, IP if present is the rest.
                parts = entry.split(":")
                if len(parts) == 3:
                    host_ip, published, _ = parts
                elif len(parts) == 2:
                    published, _ = parts
                else:
                    published = parts[0]
            elif isinstance(entry, dict):
                host_ip = entry.get("host_ip")
                published = str(entry["published"]) if entry.get("published") is not None else None
            if published is None:
                # Random host port (target only) — fine, docker still
                # binds to 0.0.0.0 but the port is unguessable. Skip.
                continue
            if host_ip not in ("127.0.0.1", "::1"):
                violations.append(f"{name}: publishes {entry!r} without 127.0.0.1 bind")
    assert not violations, (
        "services with non-loopback port bindings:\n  "
        + "\n  ".join(violations)
        + "\n\nFix: prefix the port mapping with '127.0.0.1:' "
        '(e.g. "127.0.0.1:${PORT:-X}:X").'
    )


def test_dual_homed_services_are_allowlisted():
    """A service on both networks must be in DUAL_HOMED_SERVICES.

    This is the bridge surface — the path between the management and
    operational networks. Today neo4j (for graph reads from
    management) and langgraph (for HTTP sandbox transport from
    management) are the only allowed bridges. A third introduces a
    new place where prompt-injection or process compromise can pivot
    across the boundary.

    Adding to DUAL_HOMED_SERVICES requires an ADR + maintainer review.
    """
    services = _rendered_compose()["services"]
    actual_dual_homed: set[str] = set()
    for name, svc in services.items():
        nets = _service_networks(svc)
        if MGMT_NET in nets and SANDBOX_NET in nets:
            actual_dual_homed.add(name)
    unexpected = actual_dual_homed - DUAL_HOMED_SERVICES
    missing = DUAL_HOMED_SERVICES - actual_dual_homed
    msgs: list[str] = []
    if unexpected:
        msgs.append(
            "services on BOTH networks but not allowlisted: "
            f"{sorted(unexpected)}.\n"
            "Either remove the dual membership or, if it is required, "
            "open an ADR superseding the current isolation invariant "
            "and add the service name to DUAL_HOMED_SERVICES in this "
            "test."
        )
    if missing:
        msgs.append(
            "allowlisted dual-homed services no longer present on "
            f"both networks: {sorted(missing)}.\n"
            "If the architecture intentionally changed, remove the "
            "name from DUAL_HOMED_SERVICES; otherwise restore the "
            "missing network membership."
        )
    assert not msgs, "\n\n".join(msgs)
