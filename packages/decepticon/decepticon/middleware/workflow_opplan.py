"""Enforce WORKFLOW contracts against the existing OPPLAN objective lifecycle."""

from collections.abc import Callable, Sequence
from pathlib import PurePosixPath

from decepticon.workflows import WorkflowSnapshot, WorkflowStage
from decepticon_core.types.engagement import Objective, ObjectiveStatus, PlanFact

ArtifactCheck = Callable[[list[str]], str | None]


def _leaves(objectives: Sequence[Objective]) -> tuple[Objective, ...]:
    parents = {objective.parent_id for objective in objectives}
    return tuple(objective for objective in objectives if objective.id not in parents)


def validate_workflow_bindings(
    snapshot: WorkflowSnapshot, objectives: Sequence[Objective]
) -> str | None:
    """Every executable leaf names a registered stage and its declared specialist."""
    stages = {stage.id: stage for stage in snapshot.document.metadata.stages}
    if not stages:
        return None
    for objective in objectives:
        if objective.workflow_stage is not None and objective.workflow_stage not in stages:
            return f"objective {objective.id} names an unregistered workflow_stage"
    for objective in _leaves(objectives):
        stage = stages.get(objective.workflow_stage or "")
        if stage is None:
            return f"objective {objective.id} requires a registered workflow_stage"
        if objective.owner and not stage.allows(objective.owner):
            return f"workflow stage {stage.id} requires specialist {stage.specialist}"
    return None


def _artifact_path(ref: str) -> str:
    path = PurePosixPath(ref)
    return str(path if path.is_absolute() else PurePosixPath("/workspace") / path)


def _stage_artifacts_error(
    stage: WorkflowStage, objectives: Sequence[Objective], check_artifacts: ArtifactCheck
) -> str | None:
    required = [_artifact_path(ref) for ref in stage.required_artifacts]
    recorded = {_artifact_path(ref) for objective in objectives for ref in objective.evidence_refs}
    missing = set(required) - recorded
    if missing:
        return f"workflow stage {stage.id} lacks recorded artifacts: {', '.join(sorted(missing))}"
    return check_artifacts(required) if required else None


def _reachable_leaves(
    target: Objective, objectives: Sequence[Objective], facts: Sequence[PlanFact]
) -> tuple[Objective, ...]:
    by_id = {row.id: row for row in objectives}
    fact_by_id = {fact.id: fact for fact in facts}
    pending = [target.id]
    visited: set[str] = set()
    while pending:
        identifier = pending.pop()
        if identifier in visited or identifier not in by_id:
            continue
        visited.add(identifier)
        row = by_id[identifier]
        pending.extend(row.blocked_by)
        pending.extend(
            identifier
            for group in row.any_of
            for identifier in group
            if identifier in by_id and by_id[identifier].status == ObjectiveStatus.COMPLETED
        )
        pending.extend(
            fact_by_id[fact_id].producer_id
            for fact_id in row.required_fact_ids
            if fact_id in fact_by_id
            and fact_by_id[fact_id].verified
            and fact_by_id[fact_id].evidence_refs
        )
        if row.id != target.id:
            pending.extend(child.id for child in objectives if child.parent_id == row.id)
    return tuple(row for row in _leaves(objectives) if row.id in visited and row.id != target.id)


def validate_workflow_transition(
    snapshot: WorkflowSnapshot,
    objectives: Sequence[Objective],
    target: Objective,
    *,
    specialist: str,
    completing: bool,
    check_artifacts: ArtifactCheck,
    facts: Sequence[PlanFact] = (),
) -> str | None:
    """Require completed proof on this objective's OPPLAN dependency paths."""
    if not snapshot.document.metadata.stages:
        return None
    binding_error = validate_workflow_bindings(snapshot, objectives)
    if binding_error:
        return binding_error
    leaves = _leaves(objectives)
    if target.id not in {objective.id for objective in leaves}:
        return None  # Parent completion is governed by OPPLAN's child gate.
    stages = {stage.id: stage for stage in snapshot.document.metadata.stages}
    stage = stages[target.workflow_stage or ""]
    if specialist and not stage.allows(specialist):
        return f"workflow stage {stage.id} requires specialist {stage.specialist}"
    reachable = _reachable_leaves(target, objectives, facts)
    pending = list(stage.depends_on)
    visited: set[str] = set()
    while pending:
        predecessor_id = pending.pop()
        if predecessor_id in visited:
            continue
        visited.add(predecessor_id)
        predecessor = stages[predecessor_id]
        bound = tuple(
            row
            for row in reachable
            if row.workflow_stage == predecessor_id and row.status == ObjectiveStatus.COMPLETED
        )
        if not bound:
            return f"workflow prerequisite stage incomplete on dependency path: {predecessor_id}"
        errors = [_stage_artifacts_error(predecessor, (row,), check_artifacts) for row in bound]
        if all(errors):
            return errors[0]
        pending.extend(predecessor.depends_on)
    if completing:
        return _stage_artifacts_error(stage, (target,), check_artifacts)
    return None


def validate_workflow_plan(
    snapshot: WorkflowSnapshot,
    objectives: Sequence[Objective],
    check_artifacts: ArtifactCheck,
    facts: Sequence[PlanFact] = (),
) -> str | None:
    """Prevent replanning from invalidating proof for already active work."""
    error = validate_workflow_bindings(snapshot, objectives)
    if error:
        return error
    for objective in objectives:
        if objective.status in {ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.COMPLETED}:
            error = validate_workflow_transition(
                snapshot,
                objectives,
                objective,
                specialist=objective.owner,
                completing=objective.status == ObjectiveStatus.COMPLETED,
                check_artifacts=check_artifacts,
                facts=facts,
            )
            if error:
                return error
    return None
