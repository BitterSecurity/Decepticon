from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Literal, Sequence

from decepticon_core.types.engagement import Objective, ObjectiveStatus, PlanFact

type GraphIssueCode = Literal[
    "duplicate_id",
    "missing_dependency",
    "self_dependency",
    "dependency_cycle",
    "missing_parent",
    "self_parent",
    "parent_cycle",
    "completion_cycle",
    "empty_alternative",
    "duplicate_fact",
    "missing_fact",
    "missing_fact_producer",
    "unverified_fact_producer",
    "verified_fact_missing_evidence",
    "active_prerequisite_unmet",
    "parent_incomplete_children",
]


@dataclass(frozen=True, slots=True)
class GraphIssue:
    code: GraphIssueCode
    objective_id: str
    related_id: str | None = None

    def describe(self) -> str:
        suffix = f" → {self.related_id}" if self.related_id else ""
        return f"{self.code}: {self.objective_id}{suffix}"


@dataclass(frozen=True, slots=True)
class PlanInspection:
    issues: tuple[GraphIssue, ...]
    status_ready_ids: tuple[str, ...]


def unmet_prerequisites(
    objective: Objective,
    by_id: dict[str, Objective],
    facts_by_id: dict[str, PlanFact],
) -> tuple[str, ...]:
    unmet = [
        predecessor
        for predecessor in objective.blocked_by
        if by_id[predecessor].status != ObjectiveStatus.COMPLETED
    ]
    unmet.extend(
        "any_of:" + ",".join(group)
        for group in objective.any_of
        if not any(by_id[item].status == ObjectiveStatus.COMPLETED for item in group)
    )
    unmet.extend(
        "fact:" + fact_id
        for fact_id in objective.required_fact_ids
        if not (
            facts_by_id[fact_id].verified
            and facts_by_id[fact_id].evidence_refs
            and by_id[facts_by_id[fact_id].producer_id].status == ObjectiveStatus.COMPLETED
        )
    )
    return tuple(unmet)


def _cycle_issue(predecessors: dict[str, list[str]], code: GraphIssueCode) -> GraphIssue | None:
    try:
        TopologicalSorter(predecessors).prepare()
    except CycleError as error:
        match error.args:
            case (_, [str(first), *_]):
                return GraphIssue(code=code, objective_id=first)
            case _:
                return GraphIssue(code=code, objective_id="unknown")
    return None


def inspect_plan(objectives: Sequence[Objective], facts: Sequence[PlanFact] = ()) -> PlanInspection:
    by_id: dict[str, Objective] = {}
    issues: list[GraphIssue] = []
    for objective in objectives:
        if objective.id in by_id:
            issues.append(GraphIssue(code="duplicate_id", objective_id=objective.id))
        else:
            by_id[objective.id] = objective

    facts_by_id: dict[str, PlanFact] = {}
    for fact in facts:
        if fact.id in facts_by_id:
            issues.append(GraphIssue(code="duplicate_fact", objective_id=fact.id))
        facts_by_id[fact.id] = fact
        if fact.producer_id not in by_id:
            issues.append(
                GraphIssue(
                    code="missing_fact_producer",
                    objective_id=fact.id,
                    related_id=fact.producer_id,
                )
            )
        elif fact.verified and by_id[fact.producer_id].status != ObjectiveStatus.COMPLETED:
            issues.append(
                GraphIssue(
                    code="unverified_fact_producer",
                    objective_id=fact.id,
                    related_id=fact.producer_id,
                )
            )
        if fact.verified and not fact.evidence_refs:
            issues.append(GraphIssue(code="verified_fact_missing_evidence", objective_id=fact.id))

    dependencies: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for objective in objectives:
        dependencies[objective.id] = []
        parents[objective.id] = []
        for group in objective.any_of:
            if not group:
                issues.append(GraphIssue(code="empty_alternative", objective_id=objective.id))
        for fact_id in objective.required_fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is None:
                issues.append(
                    GraphIssue(code="missing_fact", objective_id=objective.id, related_id=fact_id)
                )
            else:
                dependencies[objective.id].append(fact.producer_id)
        for predecessor in [
            *objective.blocked_by,
            *(item for group in objective.any_of for item in group),
        ]:
            if predecessor == objective.id:
                issues.append(GraphIssue(code="self_dependency", objective_id=objective.id))
            elif predecessor not in by_id:
                issues.append(
                    GraphIssue(
                        code="missing_dependency",
                        objective_id=objective.id,
                        related_id=predecessor,
                    )
                )
            else:
                dependencies[objective.id].append(predecessor)
        if objective.parent_id is not None:
            if objective.parent_id == objective.id:
                issues.append(GraphIssue(code="self_parent", objective_id=objective.id))
            elif objective.parent_id not in by_id:
                issues.append(
                    GraphIssue(
                        code="missing_parent",
                        objective_id=objective.id,
                        related_id=objective.parent_id,
                    )
                )
            else:
                parents[objective.id].append(objective.parent_id)

    completion_predecessors = {
        objective_id: list(predecessors) for objective_id, predecessors in dependencies.items()
    }
    for objective in objectives:
        if objective.parent_id is not None and objective.parent_id in completion_predecessors:
            completion_predecessors[objective.parent_id].append(objective.id)

    for graph, code in (
        (dependencies, "dependency_cycle"),
        (parents, "parent_cycle"),
        (completion_predecessors, "completion_cycle"),
    ):
        cycle = _cycle_issue(graph, code)
        if cycle is not None:
            issues.append(cycle)

    if issues:
        return PlanInspection(issues=tuple(issues), status_ready_ids=())

    for objective in objectives:
        if objective.status in {ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.COMPLETED}:
            unmet = unmet_prerequisites(objective, by_id, facts_by_id)
            if unmet:
                issues.append(
                    GraphIssue(
                        code="active_prerequisite_unmet",
                        objective_id=objective.id,
                        related_id=unmet[0],
                    )
                )
        if objective.status == ObjectiveStatus.COMPLETED and any(
            child.parent_id == objective.id
            and child.status not in {ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED}
            for child in objectives
        ):
            issues.append(GraphIssue(code="parent_incomplete_children", objective_id=objective.id))
    if issues:
        return PlanInspection(issues=tuple(issues), status_ready_ids=())

    non_leaves = {objective.parent_id for objective in objectives if objective.parent_id}
    ready = sorted(
        (
            objective
            for objective in objectives
            if objective.status == ObjectiveStatus.PENDING
            and objective.id not in non_leaves
            and not unmet_prerequisites(objective, by_id, facts_by_id)
        ),
        key=lambda objective: (objective.priority, objective.id),
    )
    return PlanInspection(issues=(), status_ready_ids=tuple(objective.id for objective in ready))
