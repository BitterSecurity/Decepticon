# Versioned OPPLAN DAG

OPPLAN is the orchestrator's executable work graph for an authorized engagement. Its nodes are bounded objectives; edges express real prerequisites. Phase, priority, and parentage explain the work but do not substitute for prerequisites. A specialist can receive only a ready leaf objective, and readiness never grants scope or tool authorization.

## Planning contract

`commit_opplan` submits the complete graph with stable objective IDs and an `expected_revision`. Forward references are valid within the same commit. The graph rejects duplicate or missing IDs, self-edges, cycles, missing fact producers, and impossible parent completion order. A replan can change pending or blocked work but cannot silently rewrite active, completed, or cancelled objectives. Status changes use `update_objective`, which checks the graph again and saves a new revision. The workspace file at `/workspace/plan/opplan.json` and agent state advance only after persistence succeeds; a stale revision is rejected.

Each objective has three prerequisite forms:

- `blocked_by`: every predecessor must be completed.
- `any_of`: one completed predecessor is required from each group.
- `required_fact_ids`: each fact needs a completed producer and a recorded workspace evidence path.

The ready frontier is the pending leaf objectives that satisfy all three forms. The orchestrator still checks the current RoE, target authorization, and evidence quality before delegating. A `task()` call carries the selected `task_id` and `plan_revision`; the hosted dispatcher rejects stale revisions, non-leaf or non-executable objectives, and unmet prerequisites. The dispatcher serializes specialist calls per engagement even when multiple nodes are ready.

## Results and invalidation

Execution status and result are separate. A completed objective records a typed outcome (`finding`, `no-finding`, or `objective-met`) and existing workspace evidence. Infrastructure errors, inconclusive work, and scope refusals block the objective with a reason; they do not become findings or successful completion. `record_plan_fact` checks that a declared producer completed and an evidence path exists. It cannot establish that the artifact's contents are true; the orchestrator must inspect them.

If evidence is disproven or lost, `revoke_plan_fact` records the reason and invalidates the fact. Completed or running dependents become blocked transitively, including downstream facts produced by those objectives. An objective with another satisfied `any_of` route remains valid. Earlier evidence references and notes remain available for audit. The orchestrator reviews blocked work before retrying it.

## Compatibility and display

Existing unversioned OPPLAN files still load. The legacy single-objective mutation tools remain available for those files, while new plans use complete-graph commits. The Run panel reads objective status, prerequisites, outcomes, evidence paths, facts, and revision from live state or durable history. It labels ready candidates as plan candidates rather than authorized tasks, and opens evidence in the workspace file viewer. A durable-history view change must be applied before a new web revision queries the added fact and revision columns.
