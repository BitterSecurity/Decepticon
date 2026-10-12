# 0006. Agent-driven domain-tool container lifecycle via an ops-control sidecar

- **Status:** Proposed (revised 2026-06-06)
- **Date:** 2026-06-05 (original) / 2026-06-06 (revision — see §1' and §6)
- **Deciders:** @PurpleCHOIms
- **Related:** [ADR-0001](0001-record-architecture-decisions.md) (cites the "Docker-socket-exec sandbox → HTTP-daemon sandbox" pivot whose reasoning §1' inherits); [ADR-0005](0005-bloodhound-via-bhce-rest-client.md) (BHCE introduced as a sidecar, currently always-on); PR #236 + PR #263 (precedent for retiring docker-socket bind in favor of an out-of-network daemon); CLAUDE.md invariants on Bash-as-single-execution-surface and sandbox/management isolation

## Context

decepticon already runs each engagement objective through a fresh
specialist sub-agent.  The orchestrator agent (`decepticon`) uses
`SubAgentMiddleware` and a `task()` tool to delegate work to
`ad_operator`, `c2_operator`, `recon_operator`, etc., and each
specialist receives **only** the tool surface for its own domain —
e.g. `ad_operator` sees `AD_TOOLS` (legacy + `BHCE_TOOLS` after
PR #586), and `recon_operator` does not see those at all.  Agent-level
tool gating is therefore already in place.

What is **not** in place is the matching **container lifecycle gating**.
After ADR-0005 the BHCE sidecar (API + dedicated Neo4j) is wired into
`docker-compose.yml` without a `profiles:` clause and boots
unconditionally on `make dev` / `docker compose up`.  The same applies
in spirit to `c2-sliver` (profile-gated but with `COMPOSE_PROFILES=
c2-sliver` set in `.env.example` as the default), to a future
wireless rig service, to the Ghidra MCP sidecar, and to any other
domain-specific multi-container stateful tool.

Three problems flow from that mismatch:

1. **Idle cost.**  An engagement that never reaches AD still pays for
   BHCE's `dawgs` index build, its Neo4j heap, and the BHCE API
   process for the whole session.  Same for an engagement that never
   uses Sliver paying for the Sliver team server.

2. **Attack-surface stretch.**  A compromised sandbox network plane
   that never needed BHCE should not have BHCE's services as
   neighbours on `decepticon-net`.  Whatever isolation the network
   already provides is strictly more defensible when the unused
   service simply isn't running.

3. **Agentic model mismatch.**  decepticon's own design principle is
   *"fresh context per objective"*.  Its specialist-spawn lifecycle
   should drive its container lifecycle, not the other way around.
   The current setup is the inversion: a static set of containers
   defines what the agent could in theory reach, and tool-level
   gating only hides them.  The owner's stated intent (2026-06-05) is
   that **recon identifies AD → orchestrator decides to spawn
   ad_operator → that same decision should also bring up BHCE**.

The constraint we will not relax: the existing CLAUDE.md invariant
that *the Bash tool is the single execution surface* and that
**only the sandbox container has access to the Docker socket** for
host-level execution.  Giving the langgraph container a docker
socket bind would let any prompt-injection escalation walk straight
out of management into host control; that is a non-starter.

## Decision

We introduce a new sidecar **`ops-control`** that owns container
lifecycle on behalf of the agent system, and route specialist-driven
service activation through it.  Four sub-decisions:

1. **`opscontrol` host-binary daemon — the only process that touches
   the docker socket.**  The daemon ships as a sub-command of
   `clients/launcher/` (Go binary), runs on the host outside any
   compose-defined network, and is installed by the launcher's onboard
   flow.  It exposes a tiny HTTP API over a **Unix domain socket**
   bind-mounted into the langgraph container only:

   ```
   /var/run/decepticon-ops.sock

   POST /v1/profiles/{name}/start   → 202 Accepted (idempotent)
   POST /v1/profiles/{name}/stop    → 202 Accepted (idempotent)
   GET  /v1/profiles                → [{name, state, since}]
   GET  /v1/health                  → liveness
   ```

   No TCP exposure, no host port, no `decepticon-net` membership.
   The socket-file bind mount is the capability grant; only the
   container that receives the mount can address the API.  Containers
   on `decepticon-net` without the mount (litellm, postgres, web,
   neo4j, bhce, sliver) cannot reach `opscontrol`.

   `{name}` is matched against a server-side **allowlist** (`ad`,
   `c2-sliver`, `c2-havoc`, `reversing`, `wireless`, …).  Anything
   else returns 400 without touching docker.  Implementation calls
   `docker compose --profile <name> up -d` (or the equivalent SDK
   call) and `docker compose --profile <name> stop` *on the host*.
   No raw `docker run` / image pull / volume create / network edit —
   those surfaces never exist.

   **Why a host-binary daemon and not an ops-control sidecar
   container?**  Putting the daemon inside a container on
   `decepticon-net` (the original §1 of this ADR, 2026-06-05)
   re-introduces the socket-bind trapdoor M1 was rejected for: a
   prompt-injection that reaches the ops-control HTTP API has
   *exactly the same hop count* to host docker as one that reaches
   langgraph directly with the socket bound — the narrowness of the
   allowlist is an application-layer guard, not a trust boundary.
   This codebase already retired the equivalent pattern for command
   execution (ADR-0001, PR #236, PR #263 — *"the agent process no
   longer needs /var/run/docker.sock (a host-escape vector for any
   prompt-injection-driven RCE inside the LLM agent)"*).  The same
   reasoning applies to lifecycle authority.  External validation in
   §6 (Anthropic Claude Code, Vercel Sandbox, Fly Machines) converges
   on the same shape: capability granted by a socket file bound into
   the caller, not by network membership.

2. **Agent surface — `decepticon.tools.ops`.**  Three LangChain
   `@tool` wrappers:

   - `ops_start(profile: str) -> str`
   - `ops_stop(profile: str) -> str`
   - `ops_status() -> str`

   They speak HTTP to `ops-control` over `decepticon-net`.  Only the
   orchestrator agent (`decepticon`) carries these in its toolbox —
   specialist sub-agents do not, so a compromised sub-agent cannot
   spin up unrelated infrastructure.  The orchestrator's system
   prompt is updated to say: *"Before delegating to a specialist
   whose domain needs a sidecar service, call ops_start(<profile>)
   and wait for healthy; after the specialist returns, call
   ops_stop(<profile>) unless another pending task in the OPPLAN
   still needs it."*

3. **Default-off for every domain-specific service.**  `bhce` +
   `bhce-neo4j` gain `profiles: [ad]`.  `c2-sliver` already has
   `profiles: [c2-sliver]` but the **default value of
   `COMPOSE_PROFILES` is removed from `.env.example`** so a vanilla
   `make dev` brings up only the core plane (litellm + postgres +
   neo4j-KGStore + langgraph + sandbox + skillogy +
   `ops-control`).  Domain services are inert until something
   `ops_start`s them.

### Workload catalog

The allowlist in §1' enumerates the workloads the agent system may
spawn.  The catalog below is the full specialist-to-workload mapping
at v1.2.0 candidate scope, expanded from the §1' seed
(`ad`, `c2-sliver`, `c2-havoc`, `reversing`, `wireless`, …) to cover
the WAVE-3 specialist expansion landed in PR #542 plus the existing
16 standard specialists.

Each row is the logical workload name (the value passed to
`ops_start("<name>")` and matched against the daemon-side allowlist),
the specialist(s) that drive it, the docker-compose profile that gates
the services, the canonical service set, and which compose network
the spawned services bind to.  Workloads marked **`(future)`** are
reserved in the allowlist but their compose services are not yet
shipped; adding one is a `(profile, services)` tuple in
`docker-compose.yml` + one entry in the daemon's allowlist + (if the
service is a Decepticon-built image) one row in
`.github/workflows/release.yml`'s docker matrix.

| Workload | Driven by | Compose profile | Services | Network |
|---|---|---|---|---|
| `ad` | `ad_operator` | `[ad]` | `bhce`, `bhce-neo4j` | `decepticon-net` |
| `c2-sliver` | `postexploit`, `exploit` | `[c2-sliver]` | `c2-sliver` | `decepticon-net` |
| `c2-havoc` *(future)* | `postexploit`, `exploit` | `[c2-havoc]` | havoc team server + agent profile | `decepticon-net` |
| `reversing` | `reverser` | `[reversing]` | `rea-mcp` (REA/Ghidra image) | `sandbox-net` |
| `phishing` *(future)* | `phisher` | `[phishing]` | `gophish`, `evilginx2`, lookalike-domain proxy | `decepticon-net` |
| `mobile` *(future)* | `mobile_operator` | `[mobile]` | Android emulator + Frida server | `sandbox-net` |
| `wireless` *(future)* | `wireless_operator` | `[wireless]` | HackRF / wifi rig (host-attached) | `sandbox-net` |
| `cloud` *(future)* | `cloud_hunter` | `[cloud]` | LocalStack / minikube cloud-emulation lab | `decepticon-net` |
| `iot` *(future)* | `iot_operator` (#542) | `[iot]` | firmadyne, MQTT / CoAP brokers, IoT firmware images | `sandbox-net` |
| `ics` *(future)* | `ics_operator` (#542) | `[ics]` | OpenPLC + Modbus / S7comm / DNP3 simulators | `sandbox-net` |
| `forensics` *(future)* | `forensicator` (#542) | `[forensics]` | DFIR toolchain (Plaso, Volatility, sleuthkit) | `sandbox-net` |
| `supply-chain` *(future)* | `supply_chain_operator` (#542) | `[supply-chain]` | CI/CD lab (Gitea, Drone, signing-key store) | `decepticon-net` |

Specialists whose entire toolchain runs inside the always-on
`sandbox` container — `recon`, `analyst`, `decepticon`, `soundwave`,
`detector`, `verifier`, `vulnresearch`, `patcher`, `contract_auditor`,
`osint_operator` (#542) — do not appear in the catalog: they neither
start nor stop any sidecar.  They consume the core plane only, so
their dispatch sequence is `task()` → specialist runs against the
sandbox, with no `ops_start` / `ops_stop` wrapping.

Two cross-cutting constraints the catalog enforces:

- **Network placement.**  Adversary-facing services (the offensive
  half of every spawn) land on `sandbox-net` so a compromised target
  cannot reach the management plane.  Defense / OSINT / orchestration
  services that the agent reads from over HTTP (BHCE, gophish admin,
  LocalStack control APIs) land on `decepticon-net`.  A workload that
  needs both a target-facing service and a management-facing UI ships
  two compose services under the same profile, each on the
  appropriate network — see `bhce` (decepticon-net) + `bhce-neo4j`
  (decepticon-net only, no sandbox-net bridge) for the canonical
  pattern.
- **Workload uniqueness.**  A specialist may drive at most one
  workload at a time per engagement: `postexploit` chooses
  `c2-sliver` *or* `c2-havoc`, not both.  This is an orchestrator-side
  policy rather than a daemon-side one — the daemon admits both names
  and an orchestrator that issues both `ops_start` calls gets both,
  but the system-prompt policy in §2 selects one based on the
  engagement's RoE / customer preference.

### Catalog extension protocol

Adding a new workload after this revision lands does **not** require
re-amending this ADR.  The shape is fixed; the catalog entries are
data, not decisions.  Extension steps for an OSS workload:

1. Add the `(profile, services)` tuple to `docker-compose.yml` under
   the appropriate compose profile, choosing `sandbox-net` vs
   `decepticon-net` per the network-placement rule above.
2. If the workload includes a Decepticon-built image, add the matrix
   row to `.github/workflows/release.yml`.
3. Add the workload name string to the daemon-side allowlist (default
   `(ad, c2-sliver, c2-havoc, reversing, phishing, mobile, wireless,
   cloud, iot, ics, forensics, supply-chain)`).  The
   default allowlist lives in the daemon binary, not in user-visible
   config, so OSS users don't gain `ops_start("ad")` rights by
   editing `.env`.
4. Add the specialist binding to the orchestrator's system prompt so
   it knows to call `ops_start(<name>)` before delegating.

Downstream / community workloads — `KubernetesBackend` namespaces,
`CloudRunBackend` services, etc. — extend the catalog through the
`decepticon.workload_backends` entry-point group (§5') rather than by
amending this ADR.

4. **HITL is an orthogonal toggle.**  An optional
   `HumanInTheLoopMiddleware` slot intercepts `ops_start` /
   `ops_stop` calls when `OPS_REQUIRE_APPROVAL=true` is set on the
   orchestrator.  Default is autonomous for unattended /
   scheduled runs; training / evaluation runs flip the toggle and
   get explicit one-click approvals via the existing HITL UI.

5. **Runtime-agnostic via `Backend` Protocol.**  `opscontrol` fronts
   a formal `Backend` Protocol with pluggable implementations
   selected by `OPS_BACKEND` (default: `docker-compose`):

   - `DockerComposeBackend` — sprint 1 only implementation, shells
     out to host `docker compose` CLI.  Covers OSS self-hosted and
     downstream per-engagement Spot VM tiers (the VM runs the OSS compose
     stack and the daemon manages it from the VM host context).
   - `KubernetesBackend` — sprint 5+, applies namespace-scoped CRDs
     through admission-validated Sigstore policy-controller.  Covers
     managed-cluster deployments (EKS / GKE / AKS / OpenShift).
   - `CloudRunBackend` / `FargateBackend` / `NomadBackend` —
     downstream / community plugins via the `decepticon.workload_backends`
     entry-point group.  Not shipped in OSS.

   The HTTP API and the `(workload, lifecycle_op)` tuple are
   backend-independent.  The agent calls `ops_start("ad")` and the
   selected backend resolves it — Docker Compose profile in OSS,
   Kubernetes CRD in managed-cluster deployments, Cloud Run service
   in serverless.  Connection resolution follows the same shape:
   `ops_start` returns a logical handle (`{workload_id,
   endpoint_url, bootstrap_token_ref}`); the agent fetches secrets
   by handle, never by network discovery, so the same agent code
   resolves Docker bridge DNS in OSS and K8s Service DNS in managed
   clusters without change.

### External validation

The host-binary-daemon-with-Unix-socket pattern in §1' and the
`Backend` Protocol in §5' converge with three independent production
exemplars and two security-framework references.  Each external
source was selected because its mechanism — not just its outcome —
matches what §1'/§5' prescribe.

- **Anthropic Claude Code sandboxing.** *"Network isolation, by only
  allowing internet access through a unix domain socket connected to
  a proxy server running outside the sandbox."*  The sandboxed
  process has its network namespace removed entirely; the only path
  to the proxy is a host-bind-mounted Unix socket — capability
  granted by socket file, not by network membership. [1]
- **Vercel Sandbox.** `Sandbox.create()` provisions a Firecracker
  microVM on Vercel's infrastructure; client code never touches the
  hypervisor or container daemon.  Per-task isolation, external
  managed control plane.  *"This provides stronger isolation than
  container-based solutions, which makes sandboxes ideal for running
  untrusted code."* [2]
- **Fly Machines API.** `api.machines.dev/v1` is the external HTTP
  control plane for per-app machine spawn.  Fly explicitly positions
  the pattern as *"a safe execution sandbox for even the sketchiest
  user-generated (or LLM-generated) code"* and recommends *"dedicated
  per-user (or per-robot) Fly apps... each containing isolated Fly
  Machines"* because *"a compromised user environment can reach
  every other Machine if consolidated."*  The per-app isolation maps
  directly to Decepticon's per-engagement Spot VM tier. [3]

The `Backend` Protocol surface (§5') and the `(workload,
lifecycle_op)` tuple (§2) match prescribed least-privilege patterns
in two authoritative security references:

- **NIST SP 800-190 §4.3.1.** *"Orchestrators should use a least
  privilege access model in which users are only granted the ability
  to perform the specific actions on the specific hosts, containers,
  and images their job roles require."*  The `(workload,
  lifecycle_op)` tuple is the canonical instance of this scoping
  prescription. [4]
- **OWASP LLM06:2025 Excessive Agency.** *"Avoid the use of
  open-ended extensions where possible (e.g., run a shell command,
  fetch a URL, etc.) and use extensions with more granular
  functionality"* because *"the scope for undesirable actions is
  very large (any other shell command could be executed)."*  A raw
  docker control API exposed to a prompt-injectable agent is the
  textbook open-ended extension this guidance disqualifies. [5]

[1] https://www.anthropic.com/engineering/claude-code-sandboxing
[2] https://vercel.com/docs/sandbox/concepts
[3] https://fly.io/docs/blueprints/per-user-dev-environments/
[4] https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-190.pdf
[5] https://genai.owasp.org/llmrisk/llm062025-excessive-agency/

### CLAUDE.md invariant update

The existing rule:

> Bash tool is the single execution surface.  All commands flow
> through `DockerSandbox.execute_tmux()` — persistent tmux sessions
> with interactive prompt detection.  Do not add side-channel exec
> paths.

is amended (separate docs PR) to:

> Bash tool is the single execution surface for in-target commands.
> `opscontrol` is the single lifecycle surface for compose-defined
> services.  The docker socket is bound to exactly one process —
> the host-installed `opscontrol` daemon — and to no container on
> any compose-defined network.  langgraph, web, cli, c2, bhce,
> sandbox all cannot reach the docker socket (sandbox already
> retired its socket bind in PR #263 per ADR-0001).

## Consequences

- **Easier**
  - Idle cost goes to zero for any service whose specialist never
    runs.  Many engagements (web-only, cloud-only, smart-contract,
    OSINT) will never start BHCE or Sliver.
  - The agent system's "fresh context per objective" principle now
    extends to "fresh process plane per objective" — closer to the
    framework's stated philosophy.
  - The attack surface of `decepticon-net` shrinks during the
    session: services that don't need to exist aren't reachable.
  - One canonical place — `ops-control`'s allowlist — describes
    every spawnable side service.  New domains plug in by adding a
    profile and an allowlist entry.

- **Harder**
  - One additional sidecar (+1 container) on the management plane.
    Mitigated by ops-control being a single tiny binary, not a stack.
  - Container start latency surfaces to the agent: BHCE cold start
    is ~30 s (Neo4j heap + goose migrations).  Specialist sub-agent
    must tolerate that gap, which means the orchestrator should call
    `ops_start` *before* `task()` rather than concurrently.
  - HMAC token lifecycle for BHCE now has to follow the lifecycle of
    the BHCE container itself — token bootstrap (admin login →
    POST /api/v2/tokens) becomes part of the start path, not the
    boot path.  We will run that inside the BHCE start handler in
    `ops-control` and pass the resulting token back to langgraph
    over `decepticon-net` (HTTP — never to disk on the host).

- **Given up**
  - Predictability of `docker compose ps` for an outside observer:
    the running container set now depends on what the engagement
    has needed so far in the current session.  We mitigate by
    `ops_status()` and a future Web Dashboard panel.
  - The convenience of a "the whole stack is up after make dev"
    mental model for new contributors.  The README onboarding flow
    will spell out the new default explicitly.

- **Migration timeline** (revised 2026-06-06)
  - Sprint 1 (revised): this revised ADR + `clients/launcher/cmd/opscontrol`
    Go daemon (HTTP-over-Unix-socket) + `Backend` Protocol +
    `DockerComposeBackend` + onboard-flow install + `tools/ops`
    LangChain wrappers (preserved from the original PR #592, with the
    httpx client switched to UDS transport via `httpx.HTTPTransport(uds=...)`).
    No behaviour change to existing services yet.  The original
    `containers/ops-control.Dockerfile` + sidecar compose entry are
    deleted (PR #592 closed as superseded).
  - Sprint 2: add `profiles: [ad]` to `bhce` + `bhce-neo4j`; remove
    `COMPOSE_PROFILES=c2-sliver` default from `.env.example`; update
    orchestrator system prompt to call `ops_start` / `ops_stop`.  At
    sprint-2 close, the catalog above's *shipped* rows (`ad`,
    `c2-sliver`, `reversing`) are reachable via `ops_start`; the
    *(future)* rows are reserved-but-inert until their workload PRs
    land (each follows the four-step extension protocol).  Workload
    PRs land independently after sprint 2 — no further ADR-0006
    amendments are required for catalog entries; the catalog is
    extended through the extension-protocol above.
  - Sprint 3: BHCE token bootstrap moves into `opscontrol`'s start
    handler; langgraph receives the token via the daemon's
    bootstrap-token handle pattern (`ops_start` returns
    `{workload_id, endpoint_url, bootstrap_token_ref}`; the agent
    fetches the secret by handle from `opscontrol`, never from disk
    or env var).
  - Sprint 4: CLAUDE.md + `docs/architecture.md` invariant edit;
    Web Dashboard `ops_status` panel.
  - Sprint 5+: `KubernetesBackend` implementation + Sigstore
    policy-controller catalog signing for FedRAMP / air-gapped tier.
    `OPS_BACKEND=kubernetes` flips the active implementation with
    no agent-side change.

## Alternatives considered

- **(M1) Give langgraph a docker socket bind so its `@tool` can run
  `docker compose up -d <profile>` directly.**  Rejected (original
  reasoning stands).  Any prompt-injection in langgraph then has full
  host-Docker control (`docker run -v /:/host …`).  This is the
  trapdoor the existing CLAUDE.md invariant exists to close.

- **(M1') Put the daemon in an `ops-control` sidecar *container* on
  `decepticon-net` with `/var/run/docker.sock` bind-mounted.**
  Initially adopted as §1 of this ADR (2026-06-05); **revised out
  2026-06-06 — see §1' and §6.**  The narrowness of the allowlist
  HTTP API is application-layer, not architectural: a
  prompt-injection that reaches the ops-control HTTP API on
  `decepticon-net` has the same effective control over the host
  docker daemon as one that reaches langgraph directly with the
  socket bound — the hop count to host root is identical.  The
  Decepticon-side precedent (ADR-0001, PR #236, PR #263 retiring the
  equivalent pattern for command execution) and the external
  validation cited in §6 converge on host-binary daemon + Unix
  socket as the correct alternative.  M1' is preserved in this list
  as an explicit audit-trail entry because it was the original
  decision and the revision matters for reviewers comparing the two
  shapes.

- **(M3) Human-in-the-loop only — no autonomous lifecycle.**
  Rejected as the *default*; the agent system has to be able to run
  unattended (scheduled engagements, long-running automated runs).
  Folded back in as the `OPS_REQUIRE_APPROVAL` toggle so HITL is
  available without being forced.

- **(M4) Move lifecycle into the host-side Go launcher
  (`clients/launcher`) and have the launcher carry the lifecycle
  daemon.**  **Adopted in revised form per §1'** (2026-06-06).  The
  launcher gains a daemon sub-command (`decepticon opscontrol
  daemon`) that the onboard flow installs and supervises.  The
  original objection ("turning the launcher into a long-lived
  bidirectional control plane is a much bigger redesign") was an
  overestimate: the daemon is a stateless HTTP server reading a
  static allowlist catalog and shelling out to `docker compose`, not
  a poller of agent state.  The OPPLAN polling shape M4 was
  originally rejected for is explicitly out of scope — the daemon
  reacts to agent-driven `ops_start` / `ops_stop` calls only.

- **(M5) Static profile-gate + manual `COMPOSE_PROFILES=` on every
  run.**  Rejected as the *only* mechanism: it solves idle cost but
  not the agentic-model mismatch.  Pieces of it (profile gating on
  the domain services) survive into sub-decision #3 of this ADR;
  what we reject is **stopping there**.

- **(M6) Run each domain stack as a separate compose project the
  operator launches by hand (`decepticon-bhce`, `decepticon-c2`,
  etc.).**  Rejected: defeats the integrated agent experience and
  doubles the secret/network plumbing.  Better revisited if/when the
  project splits OSS core from operator-managed extensions.
