"""Exercise workflow proof gates through actual registered OPPLAN/task tools."""

from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langgraph.types import Command

from decepticon.tools.opplan import _persist_opplan_to_backend, build_opplan_tools
from decepticon_core.types.engagement import ObjectiveStatus

from .opplan_helpers import (
    objective,
    state_for,
    workflow,
    write_artifact,
)


@pytest.mark.parametrize("record_required", [False, True])
def test_completion_requires_stage_artifact_and_persists_binding(
    tmp_path: Path, record_required: bool
) -> None:
    # Given
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    target = objective("target", "analyze", "reverser", ObjectiveStatus.IN_PROGRESS)
    state = state_for([target])
    write_artifact(tmp_path, "other.md")
    write_artifact(tmp_path, "proof.md")
    assert (
        _persist_opplan_to_backend(
            backend, "/workspace", state["objectives"], "test", "", revision=1
        )
        is None
    )
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "update_objective")
    # When
    result = tool.invoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": "complete",
            "args": {
                "state": state,
                "objective_id": target.id,
                "status": "completed",
                "outcome": "objective-met",
                "evidence_refs": [
                    "/workspace/evidence/proof.md"
                    if record_required
                    else "/workspace/evidence/other.md"
                ],
            },
        }
    )
    # Then
    assert isinstance(result, Command)
    assert result.update is not None
    assert (result.update["messages"][0].status != "error") is record_required
    import json

    stored = json.loads((tmp_path / "workspace/plan/opplan.json").read_text())
    assert stored["objectives"][0]["status"] == ("completed" if record_required else "in-progress")
    assert stored["objectives"][0]["workflow_stage"] == "analyze"


@pytest.mark.parametrize("stage", ["analyze", "unrelated"])
def test_commit_validates_and_persists_workflow_binding(tmp_path: Path, stage: str) -> None:
    # Given
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    row = objective("new", stage, "reverser", ObjectiveStatus.PENDING).model_dump(
        mode="json", exclude={"id"}
    )
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "commit_opplan")
    state = state_for([])
    state["plan_revision"] = 0
    # When
    result = tool.invoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": "commit",
            "args": {"state": state, "objectives": [row], "expected_revision": 0},
        }
    )
    # Then
    assert isinstance(result, Command)
    assert result.update is not None
    assert (result.update["messages"][0].status != "error") is (stage == "analyze")
    assert (tmp_path / "workspace/plan/opplan.json").exists() is (stage == "analyze")


def test_status_update_cannot_bypass_dispatch_prerequisites(tmp_path: Path) -> None:
    # Given
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.IN_PROGRESS)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    state = state_for([producer, target])
    assert (
        _persist_opplan_to_backend(
            backend, "/workspace", state["objectives"], "test", "", revision=1
        )
        is None
    )
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "update_objective")
    # When
    result = tool.invoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": "start",
            "args": {"state": state, "objective_id": target.id, "status": "in-progress"},
        }
    )
    # Then
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["messages"][0].status == "error"
    import json

    stored = json.loads((tmp_path / "workspace/plan/opplan.json").read_text())
    assert stored["objectives"][1]["status"] == "pending"


def test_new_pending_proof_preserves_completed_verification() -> None:
    from decepticon.middleware.workflow_opplan import validate_workflow_plan

    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.COMPLETED)
    target.evidence_refs = ["/workspace/evidence/verification.md"]
    sibling = objective("new-proof", "analyze", "reverser", ObjectiveStatus.PENDING)
    assert (
        validate_workflow_plan(workflow(), [producer, target, sibling], lambda refs: None) is None
    )


def test_commit_appends_independent_proof_after_completed_verification(tmp_path: Path) -> None:
    import json

    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    verified = objective("verified", "verify", "finding_verifier", ObjectiveStatus.COMPLETED)
    verified.evidence_refs = ["/workspace/evidence/verification.md"]
    write_artifact(tmp_path, "proof.md")
    write_artifact(tmp_path, "verification.md")
    state = state_for([producer, verified])
    assert (
        _persist_opplan_to_backend(
            backend, "/workspace", state["objectives"], "test", "", revision=1
        )
        is None
    )
    pending = objective("pending", "analyze", "reverser", ObjectiveStatus.PENDING)
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "commit_opplan")
    result = tool.invoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": "append",
            "args": {
                "state": state,
                "expected_revision": 1,
                "objectives": [
                    *state["objectives"],
                    pending.model_dump(mode="json", exclude={"id"}),
                ],
            },
        }
    )
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["messages"][0].status != "error"
    saved = json.loads((tmp_path / "workspace/plan/opplan.json").read_text())
    assert {row["title"]: row["status"] for row in saved["objectives"]} == {
        "producer": "completed",
        "verified": "completed",
        "pending": "pending",
    }
