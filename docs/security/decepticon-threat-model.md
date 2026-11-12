# Threat Model — Decepticon as a Target

> Decepticon is an autonomous offensive tool. That makes it a high-value
> target. This document is the STRIDE walk of Decepticon's own attack
> surface so operators, plugin authors, and multi-tenant deployers can reason
> about compromise paths against the agent itself.

## Scope

Three trust planes:

1. **Operator plane.** The human running Decepticon, their workstation,
   their Claude/OpenAI/Codex OAuth tokens, the `.env` file with API
   keys.
2. **Management plane.** Everything on `decepticon-net`: LangGraph,
   LiteLLM, PostgreSQL, Neo4j.
3. **Operational plane.** Everything on `sandbox-net`: the Kali
   sandbox, Sliver C2 server (when active), target infrastructure.

Bridges between planes are the highest-impact compromise paths.

## Bridges

```
                       OPERATOR
                          │
                          │ keys, OAuth, RoE authoring
                          ▼
            ┌────────────────────────────┐
            │   decepticon-net (mgmt)    │
            │                            │
            │  langgraph ──► litellm ──► [Anthropic/OpenAI/...]
            │      │           ▲
            │      │           │ holds every API key + OAuth file
            │      │           │
            │      ▼           │
            │   neo4j  ◄───────────────────────────┐
            │      │                                │
            │      │ DUAL-HOMED                     │
            │      │                                │
            │      ▼                                │
            │            sandbox-net ──────────────┘
            │              │
            │              │ kg_query, kg_add_*
            │              │ over Bolt
            │              ▼
            │           sandbox (Kali)
            │              │
            │              │ HTTP requests, file reads, banner grabs
            │              ▼
            │           TARGETS (legitimate threat surface)
            └────────────────────────────┘
```

The following bridges are relevant to the threat model (not a proof of current isolation):

| Bridge | Carries | Compromised → |
|--------|---------|----------------|
| **Neo4j dual-homing** | Cypher / APOC procedures | sandbox → mgmt plane RCE if APOC procedures are unrestricted |
| **LiteLLM** | OAuth files + provider API keys | full LLM-provider compromise; engagement spend; cross-customer cred theft |
| **Plugin entry-points** | Arbitrary Python on `import decepticon` | `pip install` of one malicious bundle → agent-startup RCE |

LangGraph also joins `sandbox-net` to reach the HTTP daemon. The diagram above
omits that direct network membership; Neo4j is not the only cross-plane network
surface. The operator CLI still mounts Docker's socket for service control.
Use [current architecture](../architecture.md) and the actual Compose networks,
ports, authentication and mounts when assessing reachability. “Done” labels in
this historical STRIDE table refer to individual changes, not proof that a threat
class has been eliminated. Current defaults are in
[security controls](security-controls.md).

## STRIDE walk

Per-asset analysis.

### Asset: Neo4j

| Threat | Vector | Mitigation | Status |
|--------|--------|-----------|--------|
| **Spoofing** | Shared single-user Cypher creds across sandbox + mgmt | Per-engagement Cypher user (planned, see "Future hardening" below) | Open |
| **Tampering** | Cypher injection via interpolated label | `_apoc_safety.ensure_safe` + parameter-bound queries | Done ([apoc-safety](../../packages/decepticon/decepticon/tools/research/_apoc_safety.py)) |
| **Repudiation** | No audit of Cypher writes per engagement | RoE audit ledger covers tool calls; KG-internal writes not yet logged | Partial |
| **Information disclosure** | `apoc.cypher.runFile('file:///proc/self/environ')` reads mgmt env | APOC allowlist + file-I/O procedures disabled | Done ([hardening](./neo4j-hardening.md)) |
| **Denial of service** | Heavy `MATCH (n)` traversal | `cypher.statistics_divergence` heuristic + query timeouts | Partial — Neo4j's default tx timeout applies; no per-tool timeout |
| **Elevation of privilege** | `apoc.systemdb.execute` reaches system DB | Procedure denylist | Done |

### Asset: LiteLLM container

| Threat | Vector | Mitigation | Status |
|--------|--------|-----------|--------|
| **Spoofing** | Default master key `sk-decepticon-master` documented | `.env.example` warns | Partial — operator must rotate |
| **Tampering** | LiteLLM admin UI mutates routing | Master key never exposed to sandbox network | Done |
| **Information disclosure** | OAuth credential files bind-mounted (Claude/Codex) | Bind RO; LiteLLM container itself is on `decepticon-net` only | Done |
| **Information disclosure** | Health endpoint hardcoded master key as fallback | Removed; health refuses without explicit `LITELLM_API_KEY` | Done |
| **Denial of service** | Per-engagement model-tier spend not capped | `BudgetEnforcementMiddleware` (in `_BASE_SLOTS`) hard-pauses at the per-engagement / per-agent USD cap | Done — opt-in via `DECEPTICON_BUDGET__*` ([security-controls](./security-controls.md)) |
| **Elevation of privilege** | Compromised LiteLLM → upstream provider impersonation | Provider keys held server-side; client can't extract | Done (LiteLLM design) |

### Asset: Sandbox container

| Threat | Vector | Mitigation | Status |
|--------|--------|-----------|--------|
| **Spoofing** | Sandbox HTTP daemon has no auth by default | `SANDBOX_TOKEN` env enables Bearer auth; loopback-only deploys are fine without | Partial |
| **Tampering** | Agent's command rewritten by hostile banner | UntrustedOutputMiddleware + injection detector | Done ([prompt-injection-defense](./prompt-injection-defense.md)) |
| **Tampering** | Out-of-scope target reached | RoEEnforcementMiddleware refuses + chained audit log | Done ([RoE schema + middleware](../../packages/decepticon-core/decepticon_core/types/roe.py)) |
| **Repudiation** | Operator denies engagement actions | Engagement event log (`events.jsonl`), OpenShell supervisor OCSF records and the HMAC-chained evidence chain-of-custody manifest | Done |
| **Information disclosure** | One Kali container shared across engagements | Per-engagement sandbox containers (planned, see Tier 3) | Open |
| **Denial of service** | Tool processes can exhaust resources within their limits | Compose enforces `mem_limit: 4g` and `pids_limit: 4096` for the sandbox | Bounded, not eliminated |
| **Elevation of privilege** | Sandbox kernel exploit → host | Firecracker microVM per objective (planned, multi-tenant deployments only) | Open |

### Asset: LangGraph orchestrator

| Threat | Vector | Mitigation | Status |
|--------|--------|-----------|--------|
| **Spoofing** | Plugin entry-point hijack | `DECEPTICON_PLUGINS` env explicit allowlist; signed bundles (planned) | Partial |
| **Tampering** | Prompt-injection in subagent output reaches orchestrator | UntrustedOutputMiddleware wraps every `task()` result | Done |
| **Repudiation** | OPPLAN object reordered post-hoc | OPPLAN edits via tool calls land in the RoE audit ledger as decision records | Done |
| **Information disclosure** | OPPLAN dumped to a tool that reads `/workspace` content | EngagementFilesystemBackend scopes paths to engagement workspace | Done |
| **Denial of service** | Infinite recursion via `task()` -> orchestrator -> `task()` | Per-agent `recursion_limit` defaults (200-1000 depending on role) | Done |
| **Elevation of privilege** | Compromised orchestrator dispatches arbitrary subagents | SubAgent allowlist via `decepticon.subagents` entry-point group | Partial — relies on operator trust of installed plugins |

### Asset: Web dashboard (removed)

The OSS Next.js web dashboard (`clients/web`) was removed. Its attack surface
(unauthenticated non-localhost deploys, the `engagements/[id]/graph` route, the
health route's LiteLLM key fallback) no longer ships in the OSS stack. The
officially supported dashboard is the separate Decepticon SaaS UI, which is
assessed under its own threat model.

### Asset: Plugin author surface

| Threat | Vector | Mitigation | Status |
|--------|--------|-----------|--------|
| **Spoofing** | `pip install decepticon-plugin-evil` typosquat | Operator-explicit `DECEPTICON_PLUGINS` allowlist; no auto-enable | Done |
| **Tampering** | PluginBundle replaces ENGAGEMENT_CONTEXT or RoE middleware | `SAFETY_CRITICAL_SLOTS` gate requires `DECEPTICON_ALLOW_SAFETY_OVERRIDES=1` | Done |
| **Repudiation** | Plugin issues tool calls without audit | All tool calls flow through middleware stack; ledger captures them | Done |
| **Information disclosure** | Plugin reads `~/.config/decepticon/` | Plugin code runs in agent process; no FS sandboxing | Open |
| **Denial of service** | Plugin import hangs | Entry-point loading timeout (planned) | Open |
| **Elevation of privilege** | Plugin escalates to bypass middleware | SAFETY_CRITICAL gate; threat-model doc this file documents | Partial |

## Highest-impact compromise chains

The chains that matter, ranked by realistic damage:

1. **Hostile banner → APOC → mgmt env exfil.** A target webpage returns text that influences the agent's next `kg_query` call. Pre-hardening, agent issues `CALL apoc.cypher.runFile('file:///proc/self/environ')`; the response carries PostgreSQL creds, LiteLLM master key, OAuth tokens. The APOC allowlist reduces the procedure surface; output quarantine reduces instruction confusion. Neither is proof that every injection/pivot chain is closed.

2. **Cross-engagement data leak via web dashboard (historical — dashboard removed).** Any authenticated user opens `engagements/<their-id>/graph`; the route ran `MATCH (n) OPTIONAL MATCH (n)-[r]->(m)` against the shared Neo4j. Hostile customer reads competitor's findings. **Closed** by Prisma ownership + per-engagement label scoping (Tier 4b).

3. **Operator workstation → LiteLLM OAuth file read.** Operator's `~/.claude/.credentials.json` is bind-mounted RO into LiteLLM. Compromised host with read on that file gets the OAuth token. **Mitigation**: the file is RO mounted into LiteLLM and LangGraph, not into the sandbox; standard OS file perms (600) apply to operator host. **Status**: partial — relies on operator host hygiene.

4. **Plugin entry-point poisoning.** Operator runs `pip install decepticon-plugin-foo` from a typosquat. Bundle's `__init__.py` exfiltrates `.env` at agent startup. **Not closed by bundle activation filtering**: installed Python entry-point code can execute during loading, and unlabelled contributions can be always-load. Treat plugin installation as trusted code; signed-bundle verification remains separate.

5. **Prompt-injection-driven RCE via docker socket.** The old `DockerSandbox` transport mounted `/var/run/docker.sock` into the LangGraph container; any agent-controlled command could escape to the host. The LangGraph socket mount was removed by the HTTP-only sandbox transport rewrite (predates this PR; see [`backends/factory.py`](../../packages/decepticon/decepticon/backends/factory.py)).

## Future hardening

Tracked in this PR's roadmap, in order of priority:

1. **Per-engagement Cypher user** (`decepticon-sandbox-<engagement>`, rotating Bolt token). Caps the impact of a single sandbox compromise to that engagement's portion of the graph.
2. **Per-engagement sandbox container** (Docker SDK based lifecycle, one container per engagement open). Eliminates the "Engagement A's `.scratch/` is visible to Engagement B" leak. Tier 3 of this PR ships the design + compose hardening; full lifecycle ships in a follow-up.
3. ~~**Per-engagement budget cap**~~ — **shipped.** `BudgetEnforcementMiddleware` (in `_BASE_SLOTS`) soft-warns at a configurable fraction of the cap and hard-pauses the run at 100%, scoped per-engagement and per-agent. Enable via `DECEPTICON_BUDGET__ENGAGEMENT_USD` / `DECEPTICON_BUDGET__PER_AGENT_USD`; see [security-controls](./security-controls.md). Automatic model-tier downgrade at threshold is not implemented.
4. **Plugin bundle signature** (PluginBundle ships an Ed25519 signature; operator pins trust set; `pip install` of an unpinned bundle is rejected at boot).
5. **Firecracker microVM sandbox** (one ~125ms-boot microVM per objective). Kernel boundary instead of namespace boundary. Multi-tenant deployments only; OSS keeps Docker.
6. **Sigstore-style transparent log** for the RoE audit ledger. Today the ledger lives next to the engagement workspace; the HMAC key is operator-held. For paid engagements, ship the chain hash to a third-party transparency log (Rekor) so even the operator can't rewrite history.

## Out of scope

This document does NOT cover:

- **Target-side threat model**: that's the customer's domain.
- **OpenAI/Anthropic/Google provider trust**: Decepticon trusts the upstream LLM providers' published security posture.
- **Operator workstation hardening**: standard endpoint hygiene applies.
- **Network ingress filtering** above what compose provides: deployers responsible for firewalling beyond `127.0.0.1:*` exposed ports.

## Runtime security reference

The controls referenced throughout this STRIDE walk are documented in
detail in the runtime security reference suite:

- [Security Controls](./security-controls.md) — knob/default/verify table
  for every runtime guard, plus the budget, audit, and HITL env vars.
- [RoE Machine Enforcement](./roe-machine-enforcement.md) — OpenShell
  network scope and execution time windows.
- [HITL Approval](./hitl-approval.md) — the opt-in operator-approval gate
  for high-impact actions.

## Reporting

Found a path not covered here? Open a security advisory via GitHub. The
`SECURITY.md` at the repo root lists the disclosure contact.
