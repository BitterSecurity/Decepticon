from __future__ import annotations

from decepticon.tools.opplan_graph import inspect_plan
from decepticon_core.types.engagement import Objective, ObjectivePhase, ObjectiveStatus, PlanFact


def _objective(
    objective_id: str,
    *,
    blocked_by: list[str] | None = None,
    parent_id: str | None = None,
    status: ObjectiveStatus = ObjectiveStatus.PENDING,
    priority: int = 1,
) -> Objective:
    return Objective(
        id=objective_id,
        phase=ObjectivePhase.RECON,
        title=objective_id,
        description="Inspect the scoped service",
        acceptance_criteria=["Recorded observation"],
        priority=priority,
        status=status,
        blocked_by=blocked_by or [],
        parent_id=parent_id,
    )


def test_rejects_missing_and_self_dependencies() -> None:
    inspection = inspect_plan(
        [
            _objective("OBJ-001", blocked_by=["OBJ-999"]),
            _objective("OBJ-002", blocked_by=["OBJ-002"]),
        ]
    )

    assert {(issue.code, issue.objective_id) for issue in inspection.issues} == {
        ("missing_dependency", "OBJ-001"),
        ("self_dependency", "OBJ-002"),
    }
    assert inspection.status_ready_ids == ()


def test_rejects_dependency_and_parent_cycles() -> None:
    inspection = inspect_plan(
        [
            _objective("OBJ-001", blocked_by=["OBJ-002"], parent_id="OBJ-002"),
            _objective("OBJ-002", blocked_by=["OBJ-001"], parent_id="OBJ-001"),
        ]
    )

    assert {
        "dependency_cycle",
        "parent_cycle",
    } <= {issue.code for issue in inspection.issues}
    assert inspection.status_ready_ids == ()


def test_rejects_duplicate_id_and_missing_parent() -> None:
    inspection = inspect_plan(
        [
            _objective("OBJ-001"),
            _objective("OBJ-001"),
            _objective("OBJ-002", parent_id="OBJ-999"),
        ]
    )

    assert {issue.code for issue in inspection.issues} == {
        "duplicate_id",
        "missing_parent",
    }


def test_rejects_child_waiting_for_parent_completion() -> None:
    inspection = inspect_plan(
        [
            _objective("OBJ-001"),
            _objective("OBJ-002", parent_id="OBJ-001", blocked_by=["OBJ-001"]),
        ]
    )

    assert "completion_cycle" in {issue.code for issue in inspection.issues}
    assert inspection.status_ready_ids == ()


def test_ready_frontier_contains_only_pending_leaves_with_completed_predecessors() -> None:
    inspection = inspect_plan(
        [
            _objective("OBJ-001", status=ObjectiveStatus.COMPLETED),
            _objective("OBJ-002", blocked_by=["OBJ-001"], priority=3),
            _objective("OBJ-003", blocked_by=["OBJ-001"], priority=2),
            _objective("OBJ-004", blocked_by=["OBJ-002"], priority=1),
            _objective("OBJ-005", priority=0),
            _objective("OBJ-006", parent_id="OBJ-005", priority=4),
        ]
    )

    assert inspection.issues == ()
    assert inspection.status_ready_ids == ("OBJ-003", "OBJ-002", "OBJ-006")


def test_alternative_and_fact_gates_control_ready_frontier() -> None:
    target = _objective("OBJ-004")
    target.any_of = [["OBJ-001", "OBJ-002"]]
    target.required_fact_ids = ["FACT-001"]
    producer = _objective("OBJ-003", status=ObjectiveStatus.COMPLETED)
    facts = [
        PlanFact(
            id="FACT-001",
            producer_id="OBJ-003",
            summary="Observed in-scope service",
            evidence_refs=["/workspace/recon/SUMMARY.md"],
            verified=False,
        )
    ]
    objectives = [
        _objective("OBJ-001", status=ObjectiveStatus.COMPLETED),
        _objective("OBJ-002"),
        producer,
        target,
    ]
    assert "OBJ-004" not in inspect_plan(objectives, facts).status_ready_ids
    facts[0].verified = True
    assert "OBJ-004" in inspect_plan(objectives, facts).status_ready_ids


def test_fact_producer_cycle_is_rejected() -> None:
    target = _objective("OBJ-001")
    target.required_fact_ids = ["FACT-001"]
    facts = [PlanFact(id="FACT-001", producer_id="OBJ-001", summary="Self evidence")]
    assert "dependency_cycle" in {issue.code for issue in inspect_plan([target], facts).issues}
