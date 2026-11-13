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
  - Compute driver: `docker`. Needs the host Docker socket to create sandbox
    containers.
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

## The load-bearing risk: the Docker socket

A docker-driver gateway must reach the host Docker daemon to create sandbox
containers. Mounting `/var/run/docker.sock` into a container is a host-escape
vector — the exact reason the sandbox image dropped the docker-exec transport.
So this proposal **moves** that privilege from "nowhere" to "one standing
gateway container", and the gateway's own hardening becomes the security story.

Open questions this raises (must be answered before code):

1. **Socket exposure.** Direct `docker.sock` mount vs a socket proxy
   (restricting the gateway to only the Docker API calls it needs) vs a rootless
   / Podman driver. The gateway holds the keys to the host daemon; least
   privilege matters.
2. **Blast radius.** The gateway container's user, capabilities, and network
   reach. It must not become a second, softer path to the host.
3. **Who may talk to the gateway.** Only `langgraph` on `decepticon-net`; never
   published to the host; auth on the gateway RPC (mTLS) even on the internal
   network.

## Other open decisions

4. **Gateway image source & pinning.** Published OpenShell image pinned by
   digest, vs an install-script-fetched binary baked into a thin image. Update
   channel and how a version bump is reviewed (CODEOWNERS-gated, like the
   sandbox image digest).
5. **TLS lifecycle.** Gateway generates certs on first boot into a named volume
   vs certs baked at build. Rotation.
6. **Opt-out.** Keep `DECEPTICON_SANDBOX_PROVIDER=docker` as the documented
   non-engagement escape hatch (tests, pre-scope, resource-constrained hosts),
   with the standing gateway as the default. The boundary-less `docker` path
   still refuses nothing network-level, so it stays explicitly for
   non-engagement use.
7. **Resource cost.** The gateway is another standing container; document the
   footprint and the `docker`-provider path for small hosts.
8. **First-run UX.** Before `/scope import` the unbound placeholder is expected;
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
