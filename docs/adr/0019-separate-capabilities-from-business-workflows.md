# 0019: Separate specialist capabilities from business workflows

## Status

Proposed

## Context

Program-selected bounty work, objective-led red-team engagements, penetration
tests and vulnerability research need many of the same specialist capabilities.
Business-specific agent identities and unconditional process fragments duplicate
those capabilities and couple tool contracts to a particular engagement type.
Loading startup and lifecycle procedures as optional skills also makes process
selection depend on knowledge retrieval rather than the launch context.

## Decision

Keep stable capability-based core agents. Store reusable technical knowledge and
method interpretation in SKILL.md. Define each business playbook in its own
catalog directory containing WORKFLOW.md, with typed stages, eligible specialist
roles, prerequisites, required artifacts and role-scoped Markdown instructions.

The server selects a registered workflow ID and optional exact version through
LangGraph configuration. The runtime pins the full source and SHA-256 digest in
protected checkpoint state. Resume and child dispatch retain that snapshot;
model input cannot replace it or select arbitrary filesystem paths.

Extend the existing Objective schema with optional workflow_stage. Existing
OPPLAN remains the sole task lifecycle; dispatch and plan mutation validate
workflow requirements along the selected objective's dependency paths. Completed
work for an unrelated finding does not authorize a different finding. Supplied
source, captures or access can avoid redundant discovery without bypassing proof
and independent verification.

Existing protected, unbound OPPLAN checkpoints retain a policy-only compatibility
snapshot rather than receiving inferred stage labels or retrospective proof.
Their original acceptance criteria and evidence checks remain authoritative.

## Consequences

New business playbooks can reuse existing specialists without replacing their
identities. Workflow instructions remain distinguishable from authorization and
the existing RoE, OPPLAN, tool and environment contracts. Knowledge retrieval
cannot silently alter the selected business process.

Catalog authors must maintain explicit versions and valid stage graphs. Workflow
updates affect new runs; upgrading an existing task contract requires a separate
execution. Legacy checkpoints do not gain stage enforcement retroactively.

Downstream products pinned to an older published schema need an explicit adapter
until they adopt the updated release. The production companion uses immutable
task-to-stage bindings beside its existing OPPLAN state, with no second lifecycle.

## Alternatives considered

- Business-specific agents: duplicate capability prompts and require parallel
  changes as each role evolves.
- One global workflow document: couples independent business processes and
  enlarges every specialist's context.
- Automatic process inference from chat: permits ambiguous launch context and
  unstable resume behavior.
- A separate workflow task engine: duplicates OPPLAN readiness, evidence and
  completion semantics.
