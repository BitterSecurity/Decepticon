# Architecture

This guide describes source at remote main `96b70861`. Installation and releases
are separate; compare the deployed revision with [CHAGELOG.md](../CHAGELOG.md).
For capability, knowledge, workflow and client ownership, use the
[extension contract](design/extension-contract.md).

## Runtime topology

The default OSS deployment is defined by [docker-compose.yml](../docker-compose.yml).

| Component | Network membership | Responsibility |
| --- | --- | --- |
| LangGraph | `decepticon-net`, `sandbox-net` | Agent API, workflow execution, checkpoints, event streams |
| Kali sandbox | `sandbox-net` | HTTP daemon, terminal sessions, tools and workspace file operations |
| LiteLLM, PostgreSQL | `decepticon-net` | Model routing, credentials, spend and application persistence |
| Engagement Neo4j | Both networks | Attack graph and the Skillogy catalog, separated by graph schema/provenance |
| Skillogy REST service | `decepticon-net` | Graph-backed skill discovery and retrieval |
| Optional CLI container | `decepticon-net` | Operator interface |
| Optional Sliver and REA workloads | `sandbox-net` | C2 and reversing services |
| Optional BloodHound services | `decepticon-net` | AD analysis; BHCE has a separate Neo4j |

LangGraph reaches the sandbox daemon over HTTP (`SANDBOX_URL=http://sandbox:9999`
in Compose), through [HTTPSandbox](../packages/decepticon/decepticon/backends/http_sandbox.py).
The old DockerSandbox/docker-exec transport is gone. LangGraph has no Docker
socket mount. The operator CLI container does mount the host Docker socket for
host-owned service control; that is a different trust boundary.

Network membership is not a blanket guarantee that every management endpoint is
unreachable from the sandbox: LangGraph and engagement Neo4j also join
`sandbox-net`. Loopback host port publication does not change container-to-container
reachability. Assess network paths, application authentication and privileges
separately. The sandbox has no direct membership in `decepticon-net`.

## Agent execution and storage

Standard and optional plugin graphs are registered by
[graph_registry.py](../packages/decepticon/decepticon/graph_registry.py).
The trusted client selects scope, engagement/workspace binding and execution
workflow. The runtime pins the workflow source/version/hash to the thread.
Plan is optional; approved Red execution can start without its document bundle.
The execution orchestrator owns OPPLAN and dispatches specialists according to
its versioned dependency graph, evidence and selected workflow.

Filesystem operations go through the sandbox backend. The installed launcher
binds the selected host engagement directory at `/workspace`; a raw development
Compose stack can instead mount the workspace tree. Run state and path mapping
must agree with the physical mount. A new graph thread alone does not rebind it.
See [artifact ownership](agent-file-mapping.md) and
[file conventions](agent-file-conventions.md).

The default Compose sandbox is shared within a stack. Rebinding the workspace
does not isolate process namespaces, `/tmp`, tmux history or shared service state.
[Sandbox isolation](security/sandbox-isolation.md) distinguishes existing limits
from proposed per-engagement containers.

## Knowledge and tools

Runtime knowledge is authored in
`packages/decepticon/decepticon/skills/**/SKILL.md`. The normal middleware
assembler substitutes Skillogy for the local SkillsMiddleware; explicit opt-out
uses `DECEPTICON_USE_SKILLOGY=0`. Skillogy exposes `find_skill`, `load_skill` and
`traverse` through REST, and the default Compose service uses the same engagement
Neo4j service, not a second Skillogy-only database container. See
[Skillogy](skillogy.md) for catalog synchronization and role path restrictions.

[Knowledge graph](knowledge-graph.md) documents the actual research tools and
the optional KG middleware. Tool names in a design proposal do not establish
that those tools are registered on a live role.

The four bash tools run sandbox terminal sessions. Output bounds, completion,
background jobs and log reads are implemented in
[tools/bash](../packages/decepticon/decepticon/tools/bash/) and
[sandbox_kernel](../packages/decepticon/decepticon/sandbox_kernel/), rather than
a retired DockerSandbox class. Files and durable evidence survive independently
of model context summarization.

## Optional capabilities

- The orchestrator receives `ops_start`, `ops_status`, `ops_stop` and
  `ops_cleanup_engagement` only when the host opscontrol socket is available.
  An allowlisted workload name does not establish that a corresponding Compose
  service exists. Inspect the current Compose configuration before selecting one.
- Blue uses the separately distributed AutoBot runtime and sensor stack. The
  framework extra is `decepticon[blue]`; graph discovery merges installed agent
  entry points. `/mode blue` selects `autobot`. See [Blue Cell](features/blue-cell.md).
- The native OpenShell transport is opt-in and requires a trusted host admission
  resolver. Its presence in source does not change the default HTTP deployment;
  see [qualification and limits](validation/openshell-session-adapter-20261026.md).

## Security controls and diagnostics

The enforced machine-readable RoE refuses gated calls when its configuration is
missing; obsolete audit/warn mode values no longer choose a permissive path.
Egress provisioning is a separate best-effort mechanism; inspect applied policy
and failures instead of assuming a parser refusal proves network confinement.
See [RoE enforcement](security/roe-machine-enforcement.md),
[security controls](security/security-controls.md) and the
[threat model](security/decepticon-threat-model.md).

Use `decepticon status --json`, `decepticon doctor --json`, and
`decepticon kg-health` for service, installation and graph diagnostics. The last
command reports backend identity, graph revision/stats or an error; it does not
measure graph disk size or index health. See [CLI reference](cli-reference.md).
