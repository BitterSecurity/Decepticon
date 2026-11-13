# OpenShell gateway in `docker compose` — design proposal

Status: proposal — design only, needs maintainer decisions (security-sensitive)
before implementation. Baseline: `main` after the sandbox-provider and
RoE-boundary work (#1144, #1178, #1181). No code in this document.

## The gap

Since #1144 OpenShell is declared the **only** network-boundary authority, and
the sandbox provider defaults to `openshell` (#1178). But the default
deployment — `docker compose` / `make dogfood` — never brings up an OpenShell
gateway, and nothing injects a gateway endpoint or TLS into the `langgraph`
container. So an out-of-the-box OSS install on the default provider cannot admit
any engagement: every gated run needs a gateway that is not there.

Today OpenShell has only been exercised by running the SDK from the host
(verification stack). The declaration and the shipped deployment disagree. This
proposal makes the default deployment stand up the gateway so the default
provider actually works.

## Two transport layers (do not conflate them)

```
langgraph ──gRPC (openshell SDK, mTLS+OIDC)──▶ openshell-gateway ──Docker API (/var/run/docker.sock)──▶ host Docker ──▶ sandbox container
      [layer 1: already implemented]                        [layer 2: this proposal's decision]
```

- **Layer 1 — Decepticon ↔ gateway: gRPC via the OpenShell SDK.** `openshell`
  0.1.6 `SandboxClient(endpoint=host:port)` speaks gRPC to the gateway with
  mTLS (transport identity) and optional OIDC (user identity).
  `openshell_local.py` already uses it to create sandboxes, apply the compiled
  policy and admit runs. **This exists and works; this proposal adds no
  transport code here** — only the endpoint + TLS wiring so the containerized
  `langgraph` can reach a containerized gateway.
- **Layer 2 — gateway ↔ host Docker: the Docker API over `/var/run/docker.sock`
  (HTTP/REST, not gRPC).** The docker-driver gateway talks to the host Docker
  daemon to create sandbox containers. This is the only new privilege and the
  subject of the security decision below. Decepticon itself never touches the
  Docker API.

## Invariant: the sandbox cannot reach the gateway

The security story rests on one invariant: **the attacker workload (the agent,
inside the sandbox) can never reach the gateway's gRPC endpoint or the Docker
socket.** The socket lives only in the gateway container (layer 2); it is never
mounted into a sandbox. The gateway endpoint is on the internal compose network
and is not reachable from sandbox containers. If the agent could drive the
gateway (create arbitrary privileged containers), the sandbox concept would be
void — so "sandbox → gateway" must be structurally closed, and the gateway is a
trusted orchestrator, not a confinement target.

## Why the gateway must be a standing service (not per-engagement)

Two lifetimes, already separate in code:

- **Gateway** = infrastructure. A docker-driver OpenShell server that holds the
  Docker socket and is ready to create sandboxes. It exists for the lifetime of
  the stack, independent of any engagement — like `postgres` or `litellm`.
- **Policy** = per engagement. Created only after the operator sets scope and
  `plan/roe.json` is materialized; `compile_openshell_policy` then derives the
  `SandboxPolicy` and the resolver admits a sandbox (`openshell_local.py`).

The code already honors this split: before a scope exists the factory returns
`_UnboundOpenShellSandbox` (daemon up, execution refused); a real run with a
`thread_id` compiles the RoE and admits. **Only the standing gateway is
missing.** Policy auto-generation from scope → RoE is already implemented and
unchanged by this proposal.

```
compose up ─▶ openshell-gateway (standing, docker driver)  +  langgraph (endpoint+TLS)
operator: create engagement → set scope → /scope import → plan/roe.json
first run (thread_id) ─▶ resolver compiles roe.json → policy → sandbox admission   [already implemented]
```

This rules out the alternatives: a launcher that provisions the gateway per
onboarding (wrong lifetime — breaks on restart and multi-engagement), and a
silent `docker` fallback (weakens the "OpenShell is the only authority"
declaration). The gateway belongs to the stack.

## Proposed shape

A new standing compose service plus wiring into `langgraph`:

- **`openshell-gateway` service**
  - Image: a pinned published OpenShell gateway (>= 0.1.6). Decision below on
    source/pinning.
  - Compute driver: a non-raw-socket option (see the driver table). Recommended
    first step: `docker` behind a socket proxy; target: external compute driver.
  - Listens on the internal compose network (`decepticon-net`); not published to
    the host.
  - Generates / mounts its mTLS material to a shared volume.
- **`langgraph` wiring**
  - `DECEPTICON_OPENSHELL_ENDPOINT=openshell-gateway:<port>` (compose DNS), or a
    mounted `~/.config/openshell` so `SandboxClient.from_active_cluster()`
    resolves it.
  - Mount the gateway's client TLS material read-only.
  - `depends_on: openshell-gateway: condition: service_healthy`.
- **Sandbox images.** The gateway creates sandboxes from the Decepticon sandbox
  image (now carrying `decepticon_sandbox` + Obscura, #1181/#1176); the gateway
  must be able to pull/reference it.

## Decepticon is driver-neutral

The Decepticon side (the `openshell` SDK in `langgraph`) is a gRPC **client**:
it hands the gateway a sandbox image + a compiled policy and asks for a sandbox.
It has no Docker/containerd/CRI code. Which runtime actually builds the container
is entirely the gateway's `compute_driver` setting. So switching drivers is a
gateway-config change, not a Decepticon code change — there is nothing in the
app to "port from Docker to Podman".

## Compute driver: how layer 2 is built, ranked by host-socket exposure

OpenShell's gateway chooses one `compute_driver`. The options differ in whether
anything holds the host Docker socket:

| Driver | Holds host `docker.sock`? | Notes |
|---|---|---|
| **External driver** (`compute_driver=<name>` + `socket_path`) | **No** (gateway speaks only `compute_driver.proto` over a UDS to a separate driver process) | The gateway delegates container creation; the Docker socket, if used at all, lives only inside that driver process. Gateway/`langgraph` never touch it. OpenShell "does not start or supervise extension drivers" — the process must be provided/run by us. |
| `docker` + `grpc_endpoint` / socket proxy | **Indirect** — points at a proxied/remote Docker endpoint, not the raw host socket | Docker driver config has `grpc_endpoint` and supports remote/proxied daemons (`proxy_ca_bundle` "also works with remote Docker daemons"). A socket proxy restricts the API surface to the calls the gateway needs. |
| `podman` (rootless) | **No** — Podman **user** socket, no Docker daemon | `systemctl --user start podman.socket`; rootless sandboxes. Adds a Podman dependency. |
| `vm` (MicroVM) | **No** — hypervisor (KVM), no container socket | strongest isolation; bridge toward the SaaS/E2B path. |
| `docker` (raw) | **Yes** — `/var/run/docker.sock` mounted into the gateway | host-escape vector; the baseline we want to avoid. |

The user's framing — "run the sandbox with Docker, but don't talk to it over the
Docker API" — maps exactly onto the **external compute driver**: the gateway
speaks `compute_driver.proto` (gRPC), and a small separate process does the
Docker call behind a narrow socket. That keeps the whole chain gRPC and confines
`docker.sock` to one minimal process.

```
langgraph ──gRPC(SDK)──▶ gateway ──gRPC(compute_driver.proto, UDS)──▶ driver process ──(docker)──▶ sandbox
   [implemented]                   [external driver: gateway never holds docker.sock]
```

### Recommendation (default open for review)

- **Principle:** never mount the raw `/var/run/docker.sock` into the gateway.
- **Pragmatic default (no new dependency, no custom code):** `docker` driver
  behind a **socket proxy** (least-privilege Docker API allow-list). Works on the
  Docker-only hosts Decepticon already targets; the gateway reaches Docker through
  a restricted endpoint, not the raw socket.
- **Ideal (structurally cleanest):** the **external compute driver** — all-gRPC,
  `docker.sock` isolated in one tiny process. Cost: OpenShell does not ship or
  supervise that process, so whether a ready-made one exists or we implement a
  `compute_driver.proto` shim is an open question to settle before committing to
  it as the default.
- **Alternatives:** `podman` rootless (no socket, adds Podman) and `vm`/MicroVM
  (strongest isolation, needs KVM).
- The shipped default is a review decision; this doc recommends socket-proxy
  `docker` first for turnkey adoption, with the external driver as the target.

The "sandbox cannot reach the gateway" invariant holds for every option: the
gateway is on `decepticon-net`, never published to the host, mTLS on its gRPC;
only `langgraph` may call it.

## Other open decisions

1. **Shipped default driver.** `docker`+socket-proxy (turnkey, recommended
   first) vs the external compute driver (cleanest, needs a driver process) vs
   `podman` rootless vs `vm`. Pick the baseline and whether the external driver
   process is available or must be implemented.
2. **Gateway image source & pinning.** Published OpenShell image pinned by
   digest, vs an install-script-fetched binary baked into a thin image. Update
   channel and how a version bump is reviewed (CODEOWNERS-gated, like the
   sandbox image digest).
3. **TLS lifecycle.** Gateway generates certs on first boot into a named volume
   vs certs baked at build. Rotation.
4. **Opt-out.** Keep `DECEPTICON_SANDBOX_PROVIDER=docker` as the documented
   non-engagement escape hatch (tests, pre-scope, resource-constrained hosts),
   with the standing gateway as the default. The boundary-less `docker` path
   still refuses nothing network-level, so it stays explicitly for
   non-engagement use.
5. **Resource cost.** The gateway is another standing container; document the
   footprint and the `docker`-provider path for small hosts.
6. **First-run UX.** Before `/scope import` the unbound placeholder is expected;
   document the "boot → create engagement → import scope → work" journey in
   `docs/setup-guide.md` so an empty-workspace boot is not read as a failure.
## Non-goals

- Changing policy compilation or the resolver (already implemented).
- SaaS / E2B deployment (its gateway path is separate; see the SaaS OpenShell-E2B
  work).
- Kubernetes/Helm gateway deployment (compose only here).

## Verification plan (when implemented)

- `docker compose config` valid; gateway service healthy; `langgraph` reaches it.
- Boot with no engagement → unbound placeholder, daemon healthy, no crash loop.
- Create engagement → import scope → run: resolver compiles the RoE, admits a
  sandbox, a gated tool (bash / `web_fetch` / `browser_*`) runs behind the
  policy; an out-of-scope target is denied by the supervisor.
- Gateway not published to the host; only `langgraph` can reach it.
- On a RedOps-style shared host, verify project/port isolation does not collide.
