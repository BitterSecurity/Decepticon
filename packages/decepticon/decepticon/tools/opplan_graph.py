from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Literal, Sequence

from decepticon_core.types.engagement import Objective, ObjectiveStatus

type GraphIssueCode = Literal[
    "duplicate_id",
    "missing_dependency",
    "self_dependency",
    "dependency_cycle",
    "missing_parent",
    "self_parent",
    "parent_cycle",
    "completion_cycle",
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


def inspect_plan(objectives: Sequence[Objective]) -> PlanInspection:
    by_id: dict[str, Objective] = {}
    issues: list[GraphIssue] = []
    for objective in objectives:
        if objective.id in by_id:
            issues.append(GraphIssue(code="duplicate_id", objective_id=objective.id))
        else:
            by_id[objective.id] = objective

    dependencies: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for objective in objectives:
        dependencies[objective.id] = []
        parents[objective.id] = []
        for predecessor in objective.blocked_by:
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

    non_leaves = {objective.parent_id for objective in objectives if objective.parent_id}
    ready = sorted(
        (
            objective
            for objective in objectives
            if objective.status == ObjectiveStatus.PENDING
            and objective.id not in non_leaves
            and all(
                by_id[predecessor].status == ObjectiveStatus.COMPLETED
                for predecessor in objective.blocked_by
            )
        ),
        key=lambda objective: (objective.priority, objective.id),
    )
    return PlanInspection(issues=(), status_ready_ids=tuple(objective.id for objective in ready))
