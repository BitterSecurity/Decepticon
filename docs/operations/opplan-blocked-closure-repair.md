# Recover blocked closure after extending an OPPLAN

An existing objective submission can omit optional fields such as `notes`,
`opsec_notes`, or dependencies. Previously, validation filled these omissions
with defaults before comparing the objective to its checkpoint record. An
unchanged completed objective then appeared rewritten, so `commit_opplan`
rejected the full-plan submission before a new prerequisite could be added.

Existing IDs now inherit omitted fields from their saved objective before ID
and dependency resolution. Explicit values still replace saved values and pass
through the existing graph, workflow, revision, evidence, and immutable-objective
guards. Omitting a whole active or terminal node remains invalid. Explicitly
clearing protected fields remains invalid.

A missing operator prerequisite can therefore be added as a pending objective,
started through `update_objective`, and marked blocked with an allowed outcome
and a reason. Review the resulting plan before calling `finalize_search` with
`blocked`. The finalizer still requires an actual blocked objective, a reviewed
plan revision, and a committed report; a narrative blocker alone cannot close it.

## Verification

The regression first reproduced `Cannot rewrite or remove active/terminal
objective` for a completed objective whose saved notes were omitted. Tests cover
preservation, explicit protected edits, and the registered-tool path from plan
extension through a blocked search receipt. Existing workflow and search tests
retain the authorization, evidence, revision, and closure checks.

Local verification on 2026-11-18 passed 103 focused tests and Ruff checks. There
were three existing deprecation warnings. No hosted CI success is claimed.

An isolated copy of the affected checkpoint reproduced the rejection in the
installed runtime. A local image derived from that exact runtime, with only the
OPPLAN fix, preserved all 18 terminal nodes, added and blocked the missing
prerequisite, and produced a blocked receipt referencing report revision 4.
The test container had no network and mounted source evidence read-only. The
live checkpoint was not changed by this test.

## Local runtime deployment and rollback

A local hotfix is separate from an OSS release. The tested candidate image is
`decepticon-langgraph:opplan-hotfix-20261118`; the original image is retained as
`decepticon-langgraph:rollback-opplan-20261118`. No published version tag changed.
The installed Compose file pins the local candidate for LangGraph only. A future
configuration refresh can replace this local pin, so retain the backup until a
published release includes the fix.

Before replacement, stop LangGraph long enough to back up its data volume.
Preserve its credential mounts, workspace, workflow pins, and startup command.
Verify candidate API health and assistant registration before replacing the
service. After replacement, compare the checkpoint objectives, plan revision,
artifact registry, report revision, workflow snapshot, and search state to their
saved values. Do not resume a model run as part of an installation health check.

Rollback restores the previous Compose file and recreates LangGraph with the
original image and the same volume. This repair has no storage migration, so
routine rollback does not restore or delete checkpoint data. Retain a separate
consistent volume archive for disaster recovery. Never overwrite a changed
Compose file or restore an old volume over new work without review.

Docker CLI reference: [build an image](https://docs.docker.com/reference/cli/docker/image/build/).
