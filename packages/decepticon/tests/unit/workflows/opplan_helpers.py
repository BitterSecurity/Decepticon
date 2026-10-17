"""Exercise workflow proof gates through actual registered OPPLAN/task tools."""

from pathlib import Path
from typing import TypedDict

from deepagents.backends.filesystem import FilesystemBackend
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import JsonValue

from decepticon.middleware.opplan_dispatch import planned_task_tool
from decepticon.workflows import WorkflowSnapshot
from decepticon_core.types.engagement import Objective, ObjectivePhase, ObjectiveStatus


class PlanState(TypedDict):
    workspace_path: str
    workflow_snapshot: str
    plan_revision: int
    objectives: list[dict[str, JsonValue]]


def workflow() -> WorkflowSnapshot:
    return WorkflowSnapshot.from_source("""---
id: research
version: '1'
description: Proof-gated research
stages:
  - id: analyze
    specialist: reverser
    required_artifacts: [evidence/proof.md]
  - id: verify
    specialist: finding_verifier
    depends_on: [analyze]
    required_artifacts: [evidence/verification.md]
---
## Common
Use supplied source or PoC directly for analysis.
""")


def objective(identifier: str, stage: str, owner: str, status: ObjectiveStatus) -> Objective:
    return Objective(
        id=identifier,
        phase=ObjectivePhase.RECON,
        title=identifier,
        description="Inspect authorized input",
        acceptance_criteria=["proof"],
        priority=1,
        workflow_stage=stage,
        blocked_by=["producer"] if stage == "verify" else [],
        owner=owner,
        status=status,
        evidence_refs=["/workspace/evidence/proof.md"] if stage == "analyze" else [],
    )


def state_for(rows: list[Objective]) -> PlanState:
    return {
        "workspace_path": "/workspace",
        "workflow_snapshot": workflow().model_dump_json(),
        "plan_revision": 1,
        "objectives": [row.model_dump(mode="json") for row in rows],
    }


def task_tool(backend: FilesystemBackend, calls: list[str]) -> StructuredTool:
    def dispatch(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        calls.append(subagent_type)
        return "ran"

    async def adispatch(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        return dispatch(description, subagent_type, runtime)

    original = StructuredTool.from_function(
        name="task", func=dispatch, coroutine=adispatch, description="Dispatch specialist"
    )
    return planned_task_tool(original, backend=backend)


def envelope(state: PlanState, task_id: str, role: str):
    runtime = ToolRuntime(
        state=state,
        context=None,
        config={},
        stream_writer=lambda value: None,
        tool_call_id="dispatch",
        store=None,
    )
    return {
        "type": "tool_call",
        "name": "task",
        "id": "dispatch",
        "args": {
            "description": "handoff",
            "subagent_type": role,
            "task_id": task_id,
            "plan_revision": 1,
            "runtime": runtime,
        },
    }


def write_artifact(root: Path, name: str) -> None:
    target = root / "workspace" / "evidence" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("captured proof\n")
