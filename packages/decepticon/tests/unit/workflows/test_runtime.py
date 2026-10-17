"""Workflow boundaries and real LangChain/checkpointer execution."""

import pytest
from pydantic import ValidationError

from decepticon.middleware.workflow import WorkflowSelection, resolve_workflow
from decepticon.workflows import WorkflowCatalog, WorkflowError, WorkflowSnapshot, parse_workflow
from decepticon.workflows.dispatch import validate_workflow_stage

from .runtime_helpers import source


def test_checkpoint_keeps_source_when_catalog_changes() -> None:
    # Given
    original = WorkflowSnapshot.from_source(source(body="original"))
    catalog = WorkflowCatalog([WorkflowSnapshot.from_source(source(body="updated"))])
    # When
    resumed = resolve_workflow(catalog, WorkflowSelection(), original.model_dump_json())
    # Then
    assert resumed.sha256 == original.sha256
    assert resumed.source == original.source


@pytest.mark.parametrize(
    "selection",
    [WorkflowSelection(workflow_id="pentest"), WorkflowSelection(workflow_version="2")],
)
def test_resume_rejects_changed_selection(selection: WorkflowSelection) -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source())
    # When / Then
    with pytest.raises(WorkflowError):
        resolve_workflow(WorkflowCatalog([pin]), selection, pin.model_dump_json())


@pytest.mark.parametrize("workflow_id", ["missing", "../red-team", "/tmp/WORKFLOW.md", ""])
def test_explicit_unknown_id_fails_closed(workflow_id: str) -> None:
    # Given
    catalog = WorkflowCatalog([WorkflowSnapshot.from_source(source())])
    # When / Then
    with pytest.raises(WorkflowError):
        resolve_workflow(catalog, WorkflowSelection(workflow_id=workflow_id), None)


@pytest.mark.parametrize(
    "stages",
    [
        "[{id: a, specialist: recon}, {id: a, specialist: exploit}]",
        "[{id: a, specialist: recon, depends_on: [b]}]",
        "[{id: a, specialist: recon, depends_on: [a]}]",
        "[{id: a, specialist: recon, depends_on: [b]}, {id: b, specialist: exploit, depends_on: [a]}]",
    ],
)
def test_malformed_stage_graph_rejected(stages: str) -> None:
    # Given / When / Then
    with pytest.raises(ValidationError):
        parse_workflow(source(stages=stages))


def test_duplicate_registration_rejected() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source())
    # When / Then
    with pytest.raises(WorkflowError):
        WorkflowCatalog([pin, pin])


def test_tampered_snapshot_rejected() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source())
    altered = pin.model_dump() | {"source": source(body="changed")}
    # When / Then
    with pytest.raises(ValidationError):
        WorkflowSnapshot.model_validate(altered)


def test_role_compilation_limits_structured_stages() -> None:
    # Given
    doc = parse_workflow(
        source(stages="[{id: discover, specialist: recon}, {id: act, specialist: exploit}]")
    )
    # When
    import json

    contract = json.loads(
        doc.compile("recon").split("<workflow-contract>")[1].split("</workflow-contract>")[0]
    )
    # Then
    assert [stage["id"] for stage in contract["stages"]] == ["discover"]


def test_required_artifact_blocks_downstream_dispatch() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(
        source(
            stages="[{id: discover, specialist: recon, required_artifacts: [inventory]}, {id: act, specialist: exploit, depends_on: [discover]}]"
        )
    )
    # When
    rejection = validate_workflow_stage(
        pin,
        stage_id="act",
        specialist="exploit",
        completed_stage_ids=["discover"],
        artifact_names=[],
    )
    # Then
    assert rejection is not None


@pytest.mark.parametrize(
    ("specialist", "completed", "artifacts", "accepted"),
    [
        ("exploit", ["discover"], ["inventory"], True),
        ("recon", ["discover"], ["inventory"], False),
        ("exploit", [], ["inventory"], False),
    ],
)
def test_dispatch_uses_typed_prerequisites(
    specialist: str, completed: list[str], artifacts: list[str], accepted: bool
) -> None:
    # Given
    pin = WorkflowSnapshot.from_source(
        source(
            stages="[{id: discover, specialist: recon, required_artifacts: [inventory]}, {id: act, specialist: exploit, depends_on: [discover]}]"
        )
    )
    # When
    rejection = validate_workflow_stage(
        pin,
        stage_id="act",
        specialist=specialist,
        completed_stage_ids=completed,
        artifact_names=artifacts,
    )
    # Then
    assert (rejection is None) is accepted


def test_compiler_includes_stages_for_allowed_alternative_specialist() -> None:
    # Given
    doc = parse_workflow(
        source(stages="[{id: assess, specialist: exploit, specialists: [cloud_hunter, analyst]}]")
    )
    # When
    import json

    contract = json.loads(
        doc.compile("cloud_hunter").split("<workflow-contract>")[1].split("</workflow-contract>")[0]
    )
    # Then
    assert [stage["id"] for stage in contract["stages"]] == ["assess"]
