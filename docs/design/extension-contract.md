# Extension contract: capabilities, skills, workflows and clients

Desktop hosts may opt into the proposed [settings policy adapter](desktop-settings-policy.md).
Its scope and tool approvals come from host-owned read-only custody, not graph
input; absence of the opt-in preserves the existing client contract. Policy
validation does not acknowledge runtime tool registration.

This is the current contributor entry point for extending agents, knowledge,
business workflows and client adapters. It describes the source-tree contract;
installation and deployment of this revision are separate release steps. The
[architecture decision](../adr/0019-separate-capabilities-from-business-workflows.md)
records the rationale. Historical design notes do not override this guide.

## Ownership boundaries

| Layer | Owns | Extension location |
|---|---|---|
| Capability core | Stable role, tool/environment contracts, scope limits, truthful evidence handling | `packages/decepticon/decepticon/agents/standard/` and `agents/prompts/standard/` |
| Reusable knowledge | Technical methods, interpretation, schemas, templates and local diagnostic procedures | `packages/decepticon/decepticon/skills/**/SKILL.md` |
| Business workflow | Purpose, process stages, role assignments, dependencies, acceptance artifacts and closure | `packages/decepticon/decepticon/workflows/catalog/<id>/WORKFLOW.md` |
| OPPLAN | Objective IDs, dependencies, revision, dispatch readiness, outcomes and evidence | Existing OPPLAN schemas, middleware and tools |
| Client adapter | Operator interaction, authorized scope input, trusted workflow selection, thread/run lifecycle and presentation | CLI, web, API/library or downstream product integration |

A business process gets its own WORKFLOW.md, including optional planning. A
technical recipe may contain ordered steps in SKILL.md; it must not select the
engagement process or impose a universal mission sequence. Add a capability only
when the work needs a distinct expertise/tool boundary, not merely a different
business label. Scope enforcement, tool restrictions and human approval remain
capability/runtime contracts. Neither skill retrieval nor workflow selection
confers authorization.

## Selection, purposes and immutable pins

The trusted caller selects a registered execution `workflow_id` and optional
`workflow_version` in LangGraph `config.configurable`. The built-in execution
catalogs are `red-team`, `bug-bounty`, `pentest` and `vulnerability-research`;
`red-team` is the runtime default. Additional server-owned catalog roots use
`DECEPTICON_WORKFLOW_DIR`. A chat message, retrieved document or arbitrary path
must never become a trusted selector.

The runtime resolves the catalog, validates its frontmatter and DAG, and stores
its complete source, ID, version and SHA-256 in protected `workflow_snapshot`
checkpoint state. The compiler supplies Common instructions and only the current
role's section; the orchestrator receives all stage metadata. A resume retains
the pinned source even after catalog edits. Conflicting IDs, versions or pins
fail rather than silently change the process. Select another workflow in a new
execution thread.

`purpose: execution` is the default for existing documents. The optional
Soundwave factory uses `purpose: planning`, `stages: []`, independent
`planning_workflow_id`/`planning_workflow_version` configuration and
`planning_workflow_snapshot` state. The runtime rejects cross-purpose pins.
Autohunt's separate bootstrap factory does not inherit the interactive planning
procedure merely because it shares an internal Soundwave role.

See [workflow authoring](workflow-authoring.md) for the exact format and
[optional Plan](planning-workflow.md) for publication behavior.

## OPPLAN and specialist inheritance

WORKFLOW.md is a template and policy contract, not another task engine. The
orchestrator binds live OPPLAN objectives to `workflow_stage`; a stage ID is not
an objective ID. Real dependency paths and evidence determine readiness, not a
fixed recon → initial-access → post-exploit sequence. Supplied code, captures or
footholds can satisfy discovery; missing evidence adds prerequisites. Preserve
proof → independent verification → reporting, with finding-specific objectives
and evidence. A completed unrelated finding does not satisfy another finding's
verification prerequisite.

Static children inherit the protected workflow state. Dynamic `task()` dispatch
passes the same pin through trusted configuration; children compile their own
role-scoped view. New dispatch adapters must preserve both paths, including
resume and parallel returns. Immutable pin reducers reject conflicting child
updates. Do not reconstruct the selection from specialist prose.

OPPLAN remains responsible for mutation, revision checks and completion. See the
[versioned DAG contract](../red-team/opplan-dag.md). Existing unbound checkpoints
receive a policy-only compatibility snapshot; upgrades do not invent stage
bindings or retrospectively claim proof. Explicit factory `middleware=` overrides
replace the stack: downstream composers must deliberately retain the workflow
and authorization gates they require.

## Client and authorization contract

The [engagement session contract](engagement-session-contract.md) defines shared
thread/run identity, lifecycle operations and observation cursors for client
adapters.

Red execution needs operator-approved scope. Plan is optional and is entered
explicitly; it is not an execution prerequisite. An operator provisions canonical
`plan/roe.json`; Soundwave reads it and may draft a separate candidate but cannot
rewrite it. The seven optional planning documents use that approved scope.
Publication validates the eight-document bundle (RoE plus seven documents), emits
`planning_draft_ready`, and leaves the operator in Plan. It neither grants Red
approval nor switches modes automatically. OPPLAN is created by the execution
orchestrator, not Soundwave.

Clients translate their launch context into trusted configuration and preserve
selection on resume. They must distinguish the selected business workflow from
mode, target scope, approval and tool permission. An API accepting client input
must authenticate and validate that input before forwarding trusted selectors.
CLI launch accepts `decepticon [start] --workflow <id> --workflow-version <version>`
and `DECEPTICON_WORKFLOW_ID`/`DECEPTICON_WORKFLOW_VERSION`. Plan uses separate
`DECEPTICON_PLANNING_WORKFLOW_ID`/`DECEPTICON_PLANNING_WORKFLOW_VERSION`; MCP start
accepts optional `workflow_id`/`workflow_version` fields. A trusted registered
`RoleSpec.purpose` distinguishes `execution` (default), `planning` and
`observation`. Soundwave is planning and Blue is observation. Clients use the
read-only `GET /_decepticon/roles/{role}/purpose` metadata rather than hardcoding
one planner name; unknown roles default to execution, preserving the approval
gate. Declaring a planning/observation role is a trusted plugin decision and must
match its actual capabilities.

Operator scope writes and approvals must stay outside agent tools. A validation
endpoint may check a candidate without writing authority or approval state.
See [engagement operation](../engagement-workflow.md) and
[operator RoE provisioning](../roe-provisioning.md) for concrete commands.

## Knowledge, schemas and releases

Use the [canonical SKILL.md schema](../skill-schema.md); do not copy a stale
frontmatter table from an old design. Maintain unique names, descriptions,
subdomain/trigger metadata and applicable attribution. `allowed-tools` metadata
is not an authorization gate. Workflow metadata is separately validated by
`workflows/models.py`; shared public types belong to `decepticon-core`, while
runtime integration belongs to `decepticon`.

A Markdown edit changes source, not a running knowledge graph. Audit source,
rebuild the Skillogy catalog with the pinned taxonomy inputs, inspect identities
and content hashes, then publish through the normal image/catalog synchronization
path. Verify serving retrieval against the new catalog. Catalog synchronization
must preserve unrelated graph data. See [Skillogy](../skillogy.md).

Consumers must pin compatible core/framework/SDK and immutable image/source
revisions. A downstream adapter on an older Objective schema needs an explicit
compatibility strategy rather than assuming new fields exist. Existing workflow
pins retain their source; new runs adopt new versions. Record proposed, merged
and released changes separately in the single root CHANGELOG.md; a merge is not
a deployment and SaaS image versions are independent of OSS package versions.

## Extension recipes and meaningful verification

1. **New skill:** add a focused SKILL.md and necessary references; run the skill
   audit, build the graph and inspect exact name/path/hash retrieval. Verify role
   access and a real technical example. Do not test natural-language wording as
   a proxy for behavior.
2. **New workflow:** add one catalog document using existing roles; validate
   parsing, unknown prerequisites/cycles, role-section isolation and eligible
   specialists. Execute selection through a real client adapter; capture the
   pinned ID/version/hash, dependency rejection and evidence-backed completion.
3. **New specialist:** add a factory, stable core prompt, tools and middleware
   role mapping; register its plugin/subagent contribution as appropriate. See
   [plugin authoring](../plugin-author-guide.md). Verify scope rejection, expected
   tool availability, static and dynamic child pins, result/evidence persistence
   and independent verification by another role.
4. **New client adapter:** map operator launch choices to registered IDs; retain
   the pinned workflow across resume. Exercise direct approved Red without Plan,
   missing/invalid approval rejection, explicit Plan publication without a mode
   switch, cross-purpose rejection and conflicting resume selection. Capture the
   actual client surface and resulting run configuration/checkpoint.

Useful focused commands from the repository root include:

```bash
uv run python -m decepticon.skill_audit
uv run pytest packages/decepticon/tests/unit/prompts packages/decepticon/tests/unit/workflows -q
```

Run the relevant client and dispatch tests as well as real surface scenarios for
changed adapters. For every claimed result, retain the invocation, pass/fail
observable and artifact path. Offline catalog validation does not establish that
a deployed graph has synchronized, and prose review does not establish runtime
behavior.
