# Architecture Decision Records

This directory contains the **Architecture Decision Records** (ADRs) for
Decepticon — short, numbered, append-only documents that capture
non-obvious architectural decisions, their context, and their
consequences.

## When to write one

Write an ADR when the answer to "why is the code shaped this way?"
needs to be available to a future maintainer (human or AI) who was not
in the room when the decision was made. Concretely:

- A trade-off between two viable designs where the loser is not
  obviously bad (e.g. middleware composition order, sandbox transport
  mechanism, C2 framework selection, prompt-cache boundary placement).
- A constraint that the code relies on but does not test (e.g. an
  invariant about which services share a Docker network).
- A reversal of a previous decision — the old ADR is marked
  `Superseded` and the new one explains why.
- A policy that governs how the project is operated (e.g. which merge
  and release gates exist, the AI-assisted contribution charter).

Do **not** write an ADR for:

- Decisions that are obvious from reading the code.
- Bug fixes that do not change architecture.
- Style or formatting decisions (the project's lint config is the
  source of truth).
- Feature scoping (open an issue).

## Format

Each ADR follows the [Michael Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions):

1. **Title** — short, imperative, present tense.
2. **Status** — `Proposed` / `Accepted` / `Deprecated` / `Superseded by ADR-NNNN`.
3. **Context** — the forces at play; what makes this decision non-obvious.
4. **Decision** — what we are doing.
5. **Consequences** — what becomes easier, what becomes harder, what is
   given up.
6. **Alternatives considered** — the designs that lost, with one
   sentence each on why.

Use [`template.md`](template.md) as the starting point.

## Lifecycle

- Numbered sequentially, four digits, never renumbered: `0001-...`,
  `0002-...`. The number is allocated when the PR opens; if your draft
  PR sits for a while and another ADR lands first, renumber yours.
- File name kebab-case, derived from the title: `0007-c2-framework-selection.md`.
- Append-only. To change a decision, write a new ADR that supersedes
  the old one; do not edit the old one except to flip its `Status` to
  `Superseded by ADR-NNNN`.
- Anyone may open an ADR. Landing one at `Status: Accepted` is a
  maintainer decision, by convention rather than by a branch rule — see
  [ADR-0012](0012-retire-codeowners-merge-gate.md) for why the repo no
  longer enforces per-path review.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-pr-tiering-and-blast-radius.md) | PR tiering by blast radius | Superseded by 0012 |
| [0003](0003-ai-contributor-self-review.md) | AI-assisted contribution self-review charter | Accepted |
| [0004](0004-zero-ai-slop-policy.md) | Zero AI-slop policy — the 100% quality bar | Accepted |
| [0005](0005-bloodhound-via-bhce-rest-client.md) | Integrate BloodHound via the official BHCE REST API, not via in-house reimplementation | Accepted |
| [0006](0006-agent-driven-container-lifecycle.md) | Agent-driven domain-tool container lifecycle via an ops-control sidecar | Proposed |
| [0007](0007-ai-surface-technology-node.md) | Add a Technology KG node kind for AI-surface / tech-detection signals | Accepted |
| [0008](0008-skillogy-hard-acl-phase1a.md) | Skillogy hard path-prefix ACL (Phase 1a) | Accepted |
| [0009](0009-hitl-langgraph-native-migration.md) | Migrate HITL to LangGraph-native `interrupt()` + explicit-policy sets | Proposed |
| [0010](0010-open-web-acquisition.md) | Acquire open-web content with a sandbox-side, RoE-gated fetch engine | Proposed |
| [0011](0011-skillogy-lightrag-hybrid-retrieval.md) | Skillogy find_skill uses LightRAG-style hybrid retrieval over the existing graph | Proposed |
| [0012](0012-retire-codeowners-merge-gate.md) | Retire the CODEOWNERS merge gate; keep required CI and the release environment | Accepted |
| [0013](0013-expose-blue-observations-through-mcp.md) | Expose live Blue Cell observations through MCP | Proposed |
| [0014](0014-expose-plugin-bundles-through-mcp.md) | Expose plugin bundles through MCP | Proposed |
| [0015](0015-host-mcp-bridge-for-cli-control.md) | Host MCP bridge for CLI control | Proposed |
| [0016](0016-resume-paused-engagements-through-mcp.md) | Resume paused engagements through MCP | Proposed |
| [0017](0017-import-local-target-snapshots-through-mcp.md) | Import local target snapshots through MCP | Proposed |

Keep this index in sync when you land a new ADR.
