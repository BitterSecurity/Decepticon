# Optional Plan-mode workflow

Soundwave is a planning capability selected by the operator. Its stable core
retains scope fidelity, explicit assumptions, read-only launcher RoE, no offensive
execution and no OPPLAN ownership. The per-turn discussion, document preparation,
cross-checks and review/publication procedure lives in
`workflows/catalog/planning/WORKFLOW.md`.

The document declares `purpose: planning` and `stages: []`: it does not create
OPPLAN objectives or add a predecessor to bug-bounty/red-team/pentest/VR execution.
The existing `complete_engagement_planning` tool validates the complete publication
bundle and emits `planning_draft_ready`. Focused discussion or editing does not
require regenerating a full bundle. Publication leaves the user in Plan; execution
requires the user's explicit mode selection and its existing authorization checks.

Planning selection uses `planning_workflow_id` and optionally
`planning_workflow_version`. It is pinned in `planning_workflow_snapshot`, with the
same source/version/hash validation and role compiler as operational workflows.
Operational `workflow_id`/`workflow_snapshot` fields are ignored by the planning
resolver. A planning-purpose source cannot be selected for execution or vice
versa, including on resume. Existing workflow documents default to execution
purpose; their stored source hashes remain valid.

Only the Soundwave factory installs PlanningWorkflowMiddleware. Autohunt shares
the internal Soundwave tool/slot role but keeps its own bootstrap factory and
receives no interactive planning playbook. Explicit library middleware overrides
retain their existing full-replacement semantics and plan-only tool checks.

The SaaS adapter supplies the same core and middleware to its pinned native
Soundwave factory, preserves the assistant ID and removes the old static planning
append. Its client context chooses planning only when runRole is soundwave, for
both normal Plan conversations and next-run draft conversations. Approved direct
execution and connected-program launches retain their operational workflow choices.

On upgrade, existing Plan threads acquire a planning pin on the next call.
Subsequent catalog edits affect new Plan threads rather than changing an existing
discussion's procedure. Planning skill templates remain Skillogy knowledge;
publishing changed knowledge requires the normal catalog/image rollout.
