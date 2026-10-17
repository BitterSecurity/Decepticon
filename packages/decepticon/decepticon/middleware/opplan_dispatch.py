"""Validate an OSS `task()` delegation against the current OPPLAN DAG."""

# Deliberately no `from __future__ import annotations`: it stringifies the
# `ToolRuntime` annotations below, and langchain's injected-argument detection
# reads the raw signature rather than resolving type hints. With stringified
# annotations the wrapper is not recognised as taking an injected `runtime`, so
# validation strips it and every dispatch fails with
# `atask() missing 1 required positional argument: 'runtime'`.

from collections.abc import Mapping
from functools import partial
from typing import Any

from anyio import create_task_group, to_thread
from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.runnables.config import patch_config, set_config_context
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field, ValidationError

from decepticon.middleware.workflow_opplan import validate_workflow_transition
from decepticon.tools.opplan import _evidence_error
from decepticon.tools.opplan_graph import inspect_plan, unmet_prerequisites
from decepticon.workflows import WorkflowError, WorkflowSnapshot
from decepticon_core.types.engagement import Objective, ObjectiveStatus, PlanFact


class PlannedTaskSchema(BaseModel):
    """Explicit objective binding; a free-text ID in a prompt is insufficient."""

    description: str = Field(description="Complete subagent handoff and expected output")
    subagent_type: str = Field(description="One registered specialist type")
    task_id: str = Field(description="Current OPPLAN leaf objective ID, issued by the server")
    plan_revision: int = Field(ge=0, description="Revision observed when selecting task_id")


def validate_task_plan(
    state: Mapping[str, Any],
    *,
    task_id: str,
    plan_revision: int,
    subagent_type: str,
    backend: BackendProtocol | None = None,
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
    pin = state.get("workflow_snapshot")
    if pin is not None:
        try:
            snapshot = WorkflowSnapshot.model_validate_json(pin)
        except (ValidationError, WorkflowError) as exc:
            return f"invalid workflow snapshot: {exc}"
        return validate_workflow_transition(
            snapshot,
            objectives,
            target,
            specialist=subagent_type,
            completing=False,
            facts=facts,
            check_artifacts=partial(_evidence_error, backend, state.get("workspace_path")),
        )
    return None


def planned_task_tool(original: BaseTool, *, backend: BackendProtocol | None = None) -> BaseTool:
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
            backend=backend,
        )
        if reason:
            return reject(runtime, reason)
        if not runtime.state.get("workflow_snapshot"):
            return original.func(
                description=description, subagent_type=subagent_type, runtime=runtime
            )
        config = patch_config(
            runtime.config,
            configurable={"workflow_snapshot": runtime.state.get("workflow_snapshot")},
        )
        with set_config_context(config) as context:
            return context.run(
                original.func, description=description, subagent_type=subagent_type, runtime=runtime
            )

    async def atask(
        description: str,
        subagent_type: str,
        task_id: str,
        plan_revision: int,
        runtime: ToolRuntime,
    ) -> Any:
        reason = await to_thread.run_sync(
            partial(
                validate_task_plan,
                runtime.state,
                task_id=task_id,
                plan_revision=plan_revision,
                subagent_type=subagent_type,
                backend=backend,
            )
        )
        if reason:
            return reject(runtime, reason)
        if not runtime.state.get("workflow_snapshot"):
            return await original.coroutine(
                description=description, subagent_type=subagent_type, runtime=runtime
            )
        config = patch_config(
            runtime.config,
            configurable={"workflow_snapshot": runtime.state.get("workflow_snapshot")},
        )
        results: list[str | Command] = []

        async def invoke_original() -> None:
            results.append(
                await original.coroutine(
                    description=description, subagent_type=subagent_type, runtime=runtime
                )
            )

        with set_config_context(config) as context:
            async with create_task_group() as group:
                context.run(group.start_soon, invoke_original)
        return results[0]

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
