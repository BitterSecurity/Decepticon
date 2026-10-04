"""Validate an OSS `task()` delegation against the current OPPLAN DAG."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from decepticon.tools.opplan_graph import inspect_plan, unmet_prerequisites
from decepticon_core.types.engagement import Objective, ObjectiveStatus, PlanFact


class PlannedTaskSchema(BaseModel):
    """Explicit objective binding; a free-text ID in a prompt is insufficient."""

    description: str = Field(description="Complete subagent handoff and expected output")
    subagent_type: str = Field(description="One registered specialist type")
    task_id: str = Field(description="Current OPPLAN leaf objective ID, e.g. OBJ-003")
    plan_revision: int = Field(ge=0, description="Revision observed when selecting task_id")


def validate_task_plan(
    state: Mapping[str, Any], *, task_id: str, plan_revision: int, subagent_type: str
) -> str | None:
    """Return a concrete rejection reason, or None for a status-ready leaf.

    This validates execution order and ownership, not RoE authorization.
    """
    raw_objectives = state.get("objectives")
    if not isinstance(raw_objectives, list) or not raw_objectives:
        return "OPPLAN required before task delegation"
    revision = state.get("plan_revision", 0)
    if not isinstance(revision, int) or revision < 0 or plan_revision != revision:
        return f"stale OPPLAN revision: selected {plan_revision}, current {revision}"
    raw_facts = state.get("plan_facts") or []
    if not isinstance(raw_facts, list):
        return "invalid OPPLAN facts"
    try:
        objectives = [Objective.model_validate(row) for row in raw_objectives]
        facts = [PlanFact.model_validate(row) for row in raw_facts]
    except ValidationError as exc:
        return f"invalid OPPLAN record: {exc.errors()[0]['msg']}"
    inspection = inspect_plan(objectives, facts)
    if inspection.issues:
        return "invalid OPPLAN graph: " + "; ".join(issue.describe() for issue in inspection.issues)
    by_id = {objective.id: objective for objective in objectives}
    target = by_id.get(task_id)
    if target is None:
        return f"unknown OPPLAN objective {task_id}"
    if any(row.parent_id == task_id for row in objectives):
        return f"objective {task_id} is not a leaf"
    if target.status not in {ObjectiveStatus.PENDING, ObjectiveStatus.IN_PROGRESS}:
        return f"objective {task_id} is not executable ({target.status})"
    if target.owner and target.owner != subagent_type:
        return f"objective {task_id} is owned by {target.owner}, not {subagent_type}"
    if target.status == ObjectiveStatus.PENDING and task_id not in inspection.status_ready_ids:
        return f"objective {task_id} is waiting for prerequisites"
    unresolved = unmet_prerequisites(target, by_id, {fact.id: fact for fact in facts})
    if unresolved:
        return f"objective {task_id} is waiting for: {', '.join(unresolved)}"
    return None


def planned_task_tool(original: BaseTool) -> BaseTool:
    """Wrap Deep Agents' task closure while retaining its streaming/state behavior."""
    if original.name != "task" or original.func is None or original.coroutine is None:
        raise ValueError("expected Deep Agents task tool with sync and async handlers")

    def reject(runtime: ToolRuntime, reason: str) -> Command:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"OPPLAN dispatch rejected: {reason}",
                        tool_call_id=runtime.tool_call_id or "",
                        status="error",
                    )
                ]
            }
        )

    def task(
        description: str,
        subagent_type: str,
        task_id: str,
        plan_revision: int,
        runtime: ToolRuntime,
    ) -> Any:
        reason = validate_task_plan(
            runtime.state,
            task_id=task_id,
            plan_revision=plan_revision,
            subagent_type=subagent_type,
        )
        if reason:
            return reject(runtime, reason)
        return original.func(description=description, subagent_type=subagent_type, runtime=runtime)

    async def atask(
        description: str,
        subagent_type: str,
        task_id: str,
        plan_revision: int,
        runtime: ToolRuntime,
    ) -> Any:
        reason = validate_task_plan(
            runtime.state,
            task_id=task_id,
            plan_revision=plan_revision,
            subagent_type=subagent_type,
        )
        if reason:
            return reject(runtime, reason)
        return await original.coroutine(
            description=description,
            subagent_type=subagent_type,
            runtime=runtime,
        )

    return StructuredTool.from_function(
        name="task",
        func=task,
        coroutine=atask,
        description=(
            original.description + " Bind each dispatch to task_id and current "
            "plan_revision; the OPPLAN graph is checked before the subagent runs."
        ),
        infer_schema=False,
        args_schema=PlannedTaskSchema,
    )
