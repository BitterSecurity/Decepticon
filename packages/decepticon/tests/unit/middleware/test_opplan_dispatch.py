"""`task()` must check the live OPPLAN before starting a specialist."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.tools import StructuredTool

from decepticon.middleware.opplan_dispatch import planned_task_tool, validate_task_plan


def objective(identifier: str, **overrides):
    row = {
        "id": identifier,
        "phase": "recon",
        "title": identifier,
        "description": "Inspect scoped service",
        "acceptance_criteria": ["evidence"],
        "priority": 1,
        "status": "pending",
        "owner": "recon",
    }
    row.update(overrides)
    return row


def reason(state, task_id="OBJ-002", revision=2, role="recon"):
    return validate_task_plan(
        state,
        task_id=task_id,
        plan_revision=revision,
        subagent_type=role,
    )


def test_requires_current_ready_leaf_and_owner():
    state = {
        "plan_revision": 2,
        "objectives": [
            objective("OBJ-001", status="completed"),
            objective("OBJ-002", blocked_by=["OBJ-001"]),
        ],
    }
    assert reason(state) is None
    assert "stale" in reason(state, revision=1)
    assert "owned by" in reason(state, role="exploit")
    assert "not executable" in reason(state, task_id="OBJ-001")
    assert "unknown" in reason(state, task_id="OBJ-999")
    state["objectives"][0]["status"] = "pending"
    assert "waiting" in reason(state)


def test_rejects_nonleaf_and_unverified_fact():
    state = {
        "plan_revision": 1,
        "objectives": [
            objective("OBJ-001", status="completed"),
            objective("OBJ-002", status="pending"),
            objective("OBJ-003", parent_id="OBJ-002", required_fact_ids=["FACT-001"]),
        ],
        "plan_facts": [
            {
                "id": "FACT-001",
                "producer_id": "OBJ-001",
                "summary": "Evidence",
                "verified": False,
                "evidence_refs": ["recon/evidence/example.txt"],
            }
        ],
    }
    assert "not a leaf" in reason(state, task_id="OBJ-002", revision=1)
    assert "waiting" in reason(state, task_id="OBJ-003", revision=1)
    state["plan_facts"][0]["verified"] = True
    assert reason(state, task_id="OBJ-003", revision=1) is None


def test_wrapped_tool_schema_and_rejection_does_not_invoke_subagent():
    calls: list[str] = []

    def original(description: str, subagent_type: str, runtime):
        calls.append(description)
        return "ran"

    async def original_async(description: str, subagent_type: str, runtime):
        calls.append(description)
        return "ran"

    tool = StructuredTool.from_function(
        name="task",
        func=original,
        coroutine=original_async,
        description="Delegate",
        infer_schema=True,
    )
    wrapped = planned_task_tool(tool)
    assert {"task_id", "plan_revision"} <= set(wrapped.args)
    runtime = SimpleNamespace(state={"plan_revision": 0, "objectives": []}, tool_call_id="call-1")
    result = wrapped.func("handoff", "recon", "OBJ-001", 0, runtime)
    assert result.update["messages"][0].status == "error"
    assert calls == []
