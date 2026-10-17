"""Exercise workflow proof gates through actual registered OPPLAN/task tools."""

from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from decepticon.workflows import WorkflowSnapshot
from decepticon_core.types.engagement import ObjectiveStatus

from .opplan_helpers import (
    envelope,
    objective,
    state_for,
    task_tool,
    workflow,
    write_artifact,
)


@pytest.mark.parametrize(
    "failure",
    [
        "incomplete",
        "cancelled",
        "missing-file",
        "unrecorded",
        "missing-stage",
        "wrong-stage",
        "wrong-role",
    ],
)
def test_task_gate_rejects_before_specialist_invocation(tmp_path: Path, failure: str) -> None:
    # Given
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    if failure == "incomplete":
        producer.status = ObjectiveStatus.IN_PROGRESS
    if failure == "cancelled":
        producer.status = ObjectiveStatus.CANCELLED
    if failure == "unrecorded":
        producer.evidence_refs = []
    if failure == "missing-stage":
        target.workflow_stage = None
    if failure == "wrong-stage":
        target.workflow_stage = "unrelated"
    if failure == "wrong-role":
        target.owner = "exploit"
    if failure != "missing-file":
        write_artifact(tmp_path, "proof.md")
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = tool.invoke(envelope(state_for([producer, target]), target.id, target.owner))
    # Then
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["messages"][0].status == "error"
    assert calls == []


def test_supplied_source_enters_analysis_without_recon(tmp_path: Path) -> None:
    # Given
    target = objective("target", "analyze", "reverser", ObjectiveStatus.PENDING)
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = tool.invoke(envelope(state_for([target]), target.id, target.owner))
    # Then
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert calls == ["reverser"]


@pytest.mark.asyncio
async def test_async_dispatch_uses_real_artifacts_and_completed_proof(tmp_path: Path) -> None:
    # Given
    write_artifact(tmp_path, "proof.md")
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = await tool.ainvoke(envelope(state_for([producer, target]), target.id, target.owner))
    # Then
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert calls == ["finding_verifier"]


def test_independent_pending_proof_does_not_block_verification(tmp_path: Path) -> None:
    # Given
    write_artifact(tmp_path, "proof.md")
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    sibling = objective("sibling", "analyze", "reverser", ObjectiveStatus.IN_PROGRESS)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = tool.invoke(envelope(state_for([producer, sibling, target]), target.id, target.owner))
    # Then
    assert isinstance(result, ToolMessage)
    assert result.status == "success"
    assert calls == ["finding_verifier"]


@pytest.mark.asyncio
async def test_async_dispatch_rejects_deleted_prerequisite_artifact(tmp_path: Path) -> None:
    # Given
    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = await tool.ainvoke(envelope(state_for([producer, target]), target.id, target.owner))
    # Then
    assert isinstance(result, Command)
    assert result.update is not None
    assert result.update["messages"][0].status == "error"
    assert calls == []


@pytest.mark.parametrize("role", ["cloud_hunter", "analyst", "phisher"])
def test_task_gate_respects_catalog_specialist_alternatives(tmp_path: Path, role: str) -> None:
    # Given
    pin = WorkflowSnapshot.from_source(
        workflow().source.replace(
            "specialist: reverser", "specialist: reverser\n    specialists: [cloud_hunter, analyst]"
        )
    )
    target = objective("target", "analyze", role, ObjectiveStatus.PENDING)
    state = state_for([target])
    state["workflow_snapshot"] = pin.model_dump_json()
    calls: list[str] = []
    tool = task_tool(FilesystemBackend(root_dir=tmp_path, virtual_mode=True), calls)
    # When
    result = tool.invoke(envelope(state, target.id, role))
    # Then
    assert calls == ([] if role == "phisher" else [role])
    assert isinstance(result, Command if role == "phisher" else ToolMessage)


@pytest.mark.parametrize("dependency", ["blocked_by", "any_of", "fact", "none"])
def test_proof_requires_dependency_path(tmp_path: Path, dependency: str) -> None:
    from decepticon.middleware.workflow_opplan import validate_workflow_transition
    from decepticon_core.types.engagement import PlanFact

    producer = objective("producer", "analyze", "reverser", ObjectiveStatus.COMPLETED)
    target = objective("target", "verify", "finding_verifier", ObjectiveStatus.PENDING)
    target.blocked_by = [producer.id] if dependency == "blocked_by" else []
    target.any_of = [[producer.id]] if dependency == "any_of" else []
    target.required_fact_ids = ["proof"] if dependency == "fact" else []
    facts = [
        PlanFact(
            id="proof",
            producer_id=producer.id,
            summary="proof",
            verified=True,
            evidence_refs=producer.evidence_refs,
        )
    ]
    error = validate_workflow_transition(
        workflow(),
        [producer, target],
        target,
        specialist=target.owner,
        completing=False,
        check_artifacts=lambda refs: None,
        facts=facts,
    )
    assert (error is None) is (dependency != "none")
