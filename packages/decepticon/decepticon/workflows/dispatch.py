"""Workflow contract checks reusable by the existing OPPLAN dispatch gate."""

from collections.abc import Collection

from .models import WorkflowSnapshot


def validate_workflow_stage(
    snapshot: WorkflowSnapshot,
    *,
    stage_id: str,
    specialist: str,
    completed_stage_ids: Collection[str],
    artifact_names: Collection[str],
) -> str | None:
    """Reject dispatch until prerequisite stages have their required artifacts.

    Inputs must come from the trusted OPPLAN objective/fact projection, never
    task prose. A stage's own artifacts are completion requirements, so are
    checked when a dependent stage is dispatched, not before work begins.
    """
    stages = {stage.id: stage for stage in snapshot.document.metadata.stages}
    stage = stages.get(stage_id)
    if stage is None:
        return f"unknown workflow stage: {stage_id}"
    if not stage.allows(specialist):
        return f"workflow stage {stage_id} requires specialist {stage.specialist}"
    for prerequisite in stage.depends_on:
        if prerequisite not in completed_stage_ids:
            return f"workflow stage prerequisite incomplete: {prerequisite}"
        missing = set(stages[prerequisite].required_artifacts) - set(artifact_names)
        if missing:
            return f"workflow stage {prerequisite} missing artifacts: {', '.join(sorted(missing))}"
    return None
