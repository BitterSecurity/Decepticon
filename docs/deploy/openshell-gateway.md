# OpenShell gateway in `docker compose`

The default sandbox provider is `openshell` (`backends/providers.py`) — every
engagement sandbox is born confined by the kernel policy compiled from its
`plan/roe.json`. That provider needs a running **OpenShell gateway** to admit
against. This stack ships one as a compose service (profile `openshell`) so an
out-of-the-box install can actually run an engagement; without it the resolver
stays unbound and no run can execute.

## Two replaceable axes (don't confuse them)

- **Sandbox provider** — *what* backs the agent sandbox: `openshell` (default),
  `docker` (no network boundary; tests/benchmarks), or a plugin registered under
  the `decepticon.sandbox_providers` entry point (e.g. a hosted **E2B**
  provider). This is the Decepticon abstraction.
- **Compute engine** — *how* the OpenShell gateway creates sandbox containers
  when the provider is `openshell`: `docker` or `podman` (also `kubernetes` /
  `vm` / external driver). This page is about choosing that engine.

E2B and similar hosted runtimes are **providers** (first axis), not compute
engines. Docker vs Podman is the second axis, selected here.

## Quick start

```bash
make openshell-up                 # ENGINE=docker (default)
make openshell-up ENGINE=podman   # rootless boundary (recommended)
make openshell-down               # tear down (keeps the JWT/data dir)
```

`make openshell-up` brings the whole stack up with `--profile openshell`, sets
the engine-specific env, and points `langgraph` at the gateway
(`DECEPTICON_OPENSHELL_ENDPOINT`). Equivalent manual form:

```bash
DECEPTICON_OPENSHELL_ENDPOINT=host.docker.internal:8080 \
  docker compose --profile openshell up -d
```

## Engines

### Podman rootless — recommended

```bash
make openshell-up ENGINE=podman
```

The gateway drives the **user Podman socket**, so a sandbox escape that reaches
it maps to an unprivileged, user-namespaced uid rather than host root (the real
threat-A boundary). This is an officially supported OpenShell driver and —
verified on a real host — needs no custom daemon flags.

Prerequisites: `podman` + `passt`/`pasta` installed and the user socket running
(`systemctl --user start podman.socket`; `sudo loginctl enable-linger $USER` to
persist). The gateway container runs `--userns=keep-id` as your uid so it can
read the host-owned JWT keys and the socket. Data/JWT dir defaults to
`~/.decepticon/openshell` (user-writable; the podman `gateway.toml` is rendered
there with that path).

### Docker — supported (default)

```bash
make openshell-up            # ENGINE=docker
```

The official OpenShell default; lowest friction for existing Docker users. The
gateway holds the host Docker socket:

- **Rootful daemon (out of the box):** that socket is host-root-equivalent —
  **threat A is accepted**. Fine for a single-owner local box; for a stronger
  boundary run the daemon rootless or switch to Podman.
- **Rootless Docker (advanced):** gives the unprivileged boundary, but the
  OpenShell docker driver pins the sandbox supervisor to host networking and
  reaches the gateway at `127.0.0.1:8080`, so the gateway must be on
  `--network host` (it is here) **and** the rootless daemon must be started with
  `--disable-host-loopback=false` so `langgraph` (a bridge container) can reach
  it via `host.docker.internal`. Because of that extra per-user daemon flag,
  rootless Docker is documented but not a first-class path — prefer Podman.

Data/JWT dir is `/var/lib/openshell` (root-owned, created automatically), the
upstream convention.

## How it is wired (topology)

```
langgraph (bridge) ──host.docker.internal / host.containers.internal:8080──▶ openshell-gateway (network_mode: host)
                                                                               │  compute_driver = docker|podman
                                                                               ▼  (user) docker.sock / podman.sock
                                                                             sandbox + supervisor containers
supervisor (host-net) ──127.0.0.1:8080──▶ openshell-gateway   (callback)
```

- The gateway runs `network_mode: host` because the compute driver forces the
  sandbox supervisor onto host networking and dials the gateway at
  `127.0.0.1:8080`; the gateway must share that loopback.
- `langgraph` is a bridge container and reaches the host-net gateway via
  `host.docker.internal` (Docker) / `host.containers.internal` (Podman). The SDK
  `SandboxClient` takes `host:port` (no scheme) and runs plaintext while the
  gateway has `disable_tls`.
- **Data dir is bind-mounted at the same absolute path on host and container.**
  The gateway extracts the supervisor binary there and hands that path to the
  compute daemon as a host-side bind source, so the two must match (a named
  volume cannot satisfy this).
- The `openshell-gateway-init` one-shot runs `generate-certs` to write the
  supervisor JWT signing keys (idempotent; kept if already present).

## Security notes

- `disable_tls = true` + `allow_unauthenticated_users = true` is a **local,
  single-owner dev** posture (matches upstream's compose default). The gateway
  binds `0.0.0.0` on the host network so the bridge-side `langgraph` can reach
  it — keep the host firewalled. For any shared/remote deployment, enable mTLS
  and lock the bind down.
- The OpenShell policy (compiled from the RoE) governs the **inside** of the
  sandbox (network scope, filesystem, landlock). The engine choice governs the
  **outside** (who owns the daemon/socket). They are independent; rootless/Podman
  is what closes the post-escape socket gap, not the policy.
- Harder isolation for adversarial sandbox code is an orthogonal opt-in: the
  OpenShell `vm`/MicroVM driver, `--runtime krun`, or a memory-safe OCI runtime.

## Verified

Both engines were run end-to-end on a real host (gateway image
`ghcr.io/nvidia/openshell/gateway:latest`, 0.1.8): sandbox reaches `Completed`,
`ConnectSupervisor` returns 200, and a bridge control-plane container reaches the
gateway. Details and the rootless-Docker caveat are in
`docs/design/2026-11-13-openshell-gateway-in-compose.md`.
