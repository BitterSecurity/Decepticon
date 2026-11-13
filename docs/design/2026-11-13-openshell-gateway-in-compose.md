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

## Decided: dual-engine, Podman rootless recommended

The compute engine is **selectable** (`compute_driver = docker | podman`), matching
the existing replaceable-provider philosophy. Both ship; the recommendation and
the `make dogfood` default differ by whether the operator wants the rootless
boundary.

| Axis | Problem | Our choice |
|---|---|---|
| **Adoption** | Most self-hosters have Docker, not Podman (SO 2025: Docker 71% vs Podman 11%) | **Docker stays supported** (rootful default works out of the box, no new dependency) |
| **Socket boundary (A)** | A gateway holding the root `docker.sock` ≈ host root | the only real boundary is a **rootless** engine; **Podman rootless is the recommended path** — an escape maps to an unprivileged, namespaced UID |
| **Sandbox isolation (B)** | sandboxes run adversarial pentest code sharing the host kernel | opt-in: `vm`/MicroVM or `--runtime` swap; not the baseline |

**Decision (revised after real-host verification): offer both engines; recommend
Podman rootless for the (A) boundary; keep Docker working.** The earlier
"rootless Docker as the single default" was dropped because real-host testing
showed the OpenShell docker driver's supervisor callback is hostile to rootless
Docker networking (see verification below), whereas Podman rootless is an
officially-supported OpenShell driver that works with no custom daemon flags.

- **Podman rootless (recommended):** `compute_driver = podman`, user Podman socket,
  gateway `--userns=keep-id`. Both network edges work natively
  (`host.containers.internal`); no `/etc/docker` or RootlessKit changes. This is
  the real fix for (A).
- **Docker rootful (default-on, works today):** `compute_driver = docker`, the
  official OpenShell default; what RedOps runs. The gateway holds the host
  `docker.sock` — threat (A) is accepted and documented, with Podman/vm offered as
  the hardening upgrade. Lowest-friction for existing Docker users.
- **Docker rootless (documented, advanced):** possible but requires the gateway on
  `--network host` **and** the rootless daemon started with
  `--disable-host-loopback=false` so `langgraph` can reach the gateway. Footnoted,
  not a first-class path.
- **Isolation opt-ins for adversarial code (B):** `vm`/MicroVM (OpenShell vm
  driver), **libkrun/krun** (Podman `--runtime krun`), or **youki**. Orthogonal to
  the (A) engine decision.

The "sandbox cannot reach the gateway" invariant holds regardless: the gateway is
reachable only by `langgraph`, never published for the sandboxes to call inbound;
the supervisor's outbound callback is the one allowed path.

### Why the OpenShell policy does not replace rootless

They protect different layers and neither substitutes for the other:

- The compiled OpenShell policy (`compile_openshell_policy`:
  `network_policies` / `filesystem_policy` / `landlock` / `process`) governs the
  **inside** of the sandbox — threat (B). It cannot change who owns the host
  Docker daemon or its socket.
- rootless governs the **outside** — threat (A): if the agent escapes the
  sandbox and reaches the host `docker.sock`, a rootful daemon makes that socket
  host-root, while a rootless daemon makes it an unprivileged, namespaced user.
  That difference is decided only by *who runs dockerd*, which no in-sandbox
  policy can set.

So "just tighten the OpenShell policy" leaves (A) open; rootless is required for
the post-escape boundary.

### rootless does not make in-sandbox tooling harder

A common worry, addressed explicitly: rootless applies to **the daemon**, not to
the container's interior. A container started by a rootless daemon still runs as
**root (uid 0) inside**, so `apt-get` / `pip` installs and ordinary tool use work
exactly as on rootful Docker — the sandbox image's root-based setup is unchanged.
What changes is only the host mapping (interior uid 0 → unprivileged host uid).

The real costs are elsewhere and must be measured for a pentest sandbox:
`slirp4netns` network throughput (~50–70% of rootful), binding ports < 1024,
some volume mounts, and **genuinely privileged attack techniques** (raw kernel
operations, certain packet manipulation) that a user namespace may restrict.
`CAP_NET_RAW`-level work (e.g. `nmap -sS`) generally still works; kernel-module
or deep-privilege techniques may not. Enumerating which tools are affected is a
verification item below, not an assumption.

### Verified on a real host (rootless-nesting risk resolved)

The hard, unproven part was **rootless Docker under nesting**: the gateway runs
*inside a container* and must create sandbox containers through the rootless
daemon. Research flagged this as the fragile area (user-namespace nesting, cgroup
v2, the rootless socket store). Measured on `ra-trial` (Ubuntu, WSL2 kernel
6.18, cgroup v2, `subuid/subgid mschoi:100000:65536`, rootless daemon 29.9.2
installed via `dockerd-rootless-setuptool.sh` alongside RedOps' rootful daemon,
2026-11-13):

1. **Rootless nesting works.** A container that mounts the rootless
   `docker.sock` created a sibling sandbox container successfully
   (`sibling-sandbox-created-ok`) — the gateway's docker-out-of-docker pattern
   works through the rootless daemon. User-namespace nesting, cgroup v2 and the
   rootless socket store were not a blocker.
2. **Pentest tooling under rootless is unrestricted at the container level.**
   - Container processes still run **root inside** (`uid=0`), so `apk`/`apt`/`pip`
     tool installation is unaffected.
   - The container's effective capability set is **identical to rootful Docker's
     default** (both `CapEff=00000000a80425fb`): `CAP_NET_RAW` and
     `CAP_NET_BIND_SERVICE` present; `CAP_NET_ADMIN`, `CAP_SYS_ADMIN`,
     `CAP_SYS_PTRACE`, `CAP_SYS_MODULE`, `CAP_SYS_RAWIO` dropped — same as rootful.
   - Concrete raw-socket op verified: ICMP `ping` works, and `nmap -sS` SYN scan
     against `scanme.nmap.org` returned real results (`80/tcp open`,
     `443/tcp filtered`). The canonical "needs raw socket" pentest technique is
     fine under rootless.

   Rootless therefore changes **nothing** at the container capability layer; the
   difference is purely at the daemon/host boundary (daemon as unprivileged user,
   user-namespace remap) and the network datapath (slirp4netns). The only
   techniques lost are host-network-manipulation ones needing `CAP_NET_ADMIN`
   (iptables/routing/tun-tap) — which rootful Docker also drops by default, so no
   regression vs the current baseline. If such a technique is ever needed, route
   that engagement to a `vm`/MicroVM profile.

Known rootless-Docker constraints to document: ports < 1024, some volume mounts,
and slirp4netns networking (~50–70% of rootful throughput; rootless Docker does
not yet use pasta). macOS/WSL2 already run Docker inside a VM, so the rootless
story differs there and needs its own note.

### Gateway E2E verified for both engines (why Podman is recommended)

Beyond the nesting/capability result above, the full gateway→supervisor chain was
run on `ra-trial` with the official `ghcr.io/nvidia/openshell/gateway:latest`
image (gateway 0.1.8), 2026-11-13. Two network edges must both work: the
supervisor's **outbound callback** to the gateway, and the **control plane**
(`langgraph`) reaching the gateway.

**Rootless Docker — works only with care.** The docker driver forces the
supervisor to `network_mode=Host` + `endpoint=http://127.0.0.1:8080`. Observations:
- A host-net supervisor **cannot** reach a *bridge* gateway (published `127.0.0.1`
  port, container IP, and bridge gateway IP all refused). The gateway must
  therefore be on `--network host`; then supervisor→gateway works and a sandbox
  reaches `Completed` (`ConnectSupervisor` 200).
- But `langgraph` is a *bridge* container, and rootless Docker runs RootlessKit
  with `--disable-host-loopback` by default — `host.docker.internal` does not even
  resolve, so `langgraph` cannot reach the host-net gateway unless the operator
  restarts the rootless daemon with `--disable-host-loopback=false`. A custom
  daemon flag every user would need: this is why rootless Docker is advanced-only.

**Podman rootless — both edges work natively.** `compute_driver = podman`, user
Podman socket, gateway container `--userns=keep-id --user <uid>:<gid>` (so it can
read the host-owned JWT keys and the socket). Driver came up `rootless=true`,
`network_backend=netavark`, auto-detected `grpc_endpoint=http://127.0.0.1:8080`:
- Sandbox created and reached `Completed`; `ConnectSupervisor` → 200.
- A *bridge* control-plane container reached the gateway via
  `host.containers.internal:8081` → HTTP 200 (resolved to the host IP; Podman also
  aliases `host.docker.internal`). **No `--disable-host-loopback` change needed.**

Conclusion: Podman rootless delivers the (A) boundary with the clean networking
the docker driver lacks under rootless, so it is the recommended rootless path;
Docker stays supported (rootful default; rootless Docker documented with the
daemon-flag caveat).

### Sources

- Adoption / perf / nesting (Docker vs Podman): Stack Overflow 2025; Luca Berton
  "Podman vs Docker 2026" <https://lucaberton.com/blog/podman-vs-docker-2026/>;
  sanj.dev container runtime showdown 2025
  <https://sanj.dev/post/container-runtime-showdown-2025/>; Podman socket /
  nesting <https://www.golinuxcloud.com/podman-docker-socket/>.
- Socket boundary: Docker rootless <https://docs.docker.com/engine/security/rootless/>;
  NIST SP 800-190; socket-proxy analysis
  <https://medium.com/@ZeroCVEClub/is-mounting-the-docker-socket-a-container-security-risk-58813741c865>.
- Sandbox isolation / Rust runtimes: youki <https://github.com/youki-dev/youki>;
  libkrun/krun <https://github.com/libkrun/libkrun> and crun `krun.1`; Kata vs
  Firecracker vs gVisor <https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor>.

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
