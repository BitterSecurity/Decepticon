"""Existing objective references must preserve saved fields during plan extension."""

from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend

from decepticon.tools.opplan import _persist_opplan_to_backend, build_opplan_tools
from decepticon_core.types.engagement import Objective


def invoke_commit(tmp_path: Path, change: dict | None = None):
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    old = Objective.model_validate(
        dict(
            id="done",
            phase="recon",
            title="Scope",
            description="Approved scope",
            acceptance_criteria=["checked"],
            priority=1,
            status="completed",
            notes="Verified prior evidence",
            evidence_refs=["/workspace/evidence/scope.md"],
            blocked_by=[],
        )
    )
    state = dict(
        objectives=[old.model_dump(mode="json")], plan_revision=1, workspace_path="/workspace"
    )
    assert (
        _persist_opplan_to_backend(
            backend, "/workspace", state["objectives"], "test", "", revision=1
        )
        is None
    )
    tool = next(t for t in build_opplan_tools(backend) if t.name == "commit_opplan")
    result = tool.invoke(
        dict(
            type="tool_call",
            name=tool.name,
            id="extend",
            args=dict(
                state=state,
                expected_revision=1,
                objectives=[
                    {
                        **{
                            k: v
                            for k, v in old.model_dump(mode="json").items()
                            if k
                            in {
                                "id",
                                "phase",
                                "title",
                                "description",
                                "acceptance_criteria",
                                "priority",
                                "status",
                            }
                        },
                        **(change or {}),
                    },
                    dict(
                        phase="recon",
                        title="Missing role account",
                        description="Operator must provision account",
                        acceptance_criteria=["account available"],
                        priority=1,
                        blocked_by=["done"],
                    ),
                ],
            ),
        )
    )
    return old, result


def test_append_preserves_omitted_terminal_fields(tmp_path: Path):
    old, result = invoke_commit(tmp_path)
    assert result.update["messages"][0].status != "error", result.update["messages"][0].content
    assert result.update["objectives"][0] == old.model_dump(mode="json")
    assert len(result.update["objectives"]) == 2
    assert result.update["plan_revision"] == 2


@pytest.mark.parametrize(
    "change",
    [
        {"notes": "rewritten"},
        {"evidence_refs": []},
        {"blocked_by": [1]},
        {"status": "pending"},
        {"title": "changed"},
    ],
)
def test_explicit_terminal_changes_still_rejected(tmp_path: Path, change: dict):
    _, result = invoke_commit(tmp_path, change)
    assert result.update["messages"][0].status == "error"


def test_appended_prerequisite_can_close_search_as_blocked(tmp_path: Path, monkeypatch):
    from langchain.tools import ToolRuntime

    from decepticon.artifacts import (
        ActorContext,
        ArtifactDraft,
        ArtifactKind,
        ArtifactRegistry,
        CommitRequest,
        commit,
        create,
    )
    from decepticon.middleware.artifact_tools import persist_artifacts
    from decepticon.middleware.search_policy import SearchSession
    from decepticon.middleware.search_tools import (
        FinalizeSearchRequest,
        ReviewSearchRequest,
        build_search_tools,
    )

    old, extension = invoke_commit(tmp_path)
    assert extension.update["messages"][0].status != "error"
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    state = {k: v for k, v in extension.update.items() if k != "messages"}
    state["workspace_path"] = "/workspace"
    tools = {t.name: t for t in build_opplan_tools(backend)}
    blocked_id = state["objectives"][-1]["id"]
    started = tools["update_objective"].invoke(
        dict(
            type="tool_call",
            name="update_objective",
            id="start",
            args=dict(state=state, objective_id=blocked_id, status="in-progress"),
        )
    )
    assert started.update["messages"][0].status != "error"
    state.update({k: v for k, v in started.update.items() if k != "messages"})
    result = tools["update_objective"].invoke(
        dict(
            type="tool_call",
            name="update_objective",
            id="block",
            args=dict(
                state=state,
                objective_id=blocked_id,
                status="blocked",
                outcome="inconclusive",
                notes="Operator role account is not provisioned",
            ),
        )
    )
    assert result.update["messages"][0].status != "error", result.update["messages"][0].content
    state.update({k: v for k, v in result.update.items() if k != "messages"})
    assert state["objectives"][0] == old.model_dump(mode="json")
    registry = create(
        ArtifactRegistry(),
        ArtifactDraft(
            kind=ArtifactKind.ASSESSMENT_REPORT,
            title="Partial assessment",
            slug="partial-assessment",
            body="Blocked: operator role account missing. No authorization verdict.",
        ),
        "report",
    )
    report = next(iter(registry.artifacts.values()))
    registry = commit(
        registry,
        CommitRequest(
            document_uuid=report.document_uuid,
            expected_revision=0,
            operation_key="commit",
            reason="Record missing prerequisite",
        ),
        ActorContext(actor="operator", role="decepticon"),
    )
    registry = persist_artifacts(registry, backend, "/workspace")
    state["artifact_registry"] = registry.model_dump_json()
    state["search_state"] = SearchSession(
        thread_id="test-thread", run_id="test-run", started_at="2026-11-18T00:00:00Z"
    ).model_dump_json()
    monkeypatch.setattr("decepticon.middleware.search_tools.get_config", lambda: {})

    def runtime():
        return ToolRuntime(
            state=state,
            context=None,
            config={},
            stream_writer=lambda _: None,
            tool_call_id="review",
            store=None,
        )

    search = {t.name: t for t in build_search_tools(backend)}
    reviewed = search["review_search"].func(
        ReviewSearchRequest(
            plan_revision=state["plan_revision"],
            rationale="The operator must provision the role account",
        ),
        runtime(),
    )
    assert reviewed.update["messages"][0].status != "error"
    state["search_state"] = reviewed.update["search_state"]
    request = FinalizeSearchRequest(
        disposition="blocked",
        report_document_uuid=report.document_uuid,
        plan_revision=state["plan_revision"],
        reason="Operator account missing",
    )
    final = search["finalize_search"].func(request, runtime())
    assert final.update["messages"][0].status != "error", final.update["messages"][0].content
    import json

    assert json.loads(final.update["search_receipt"])["disposition"] == "blocked"
    # A narrative blocker cannot substitute for an actual blocked objective.
    state["objectives"] = [old.model_dump(mode="json")]
    refused = search["finalize_search"].func(request, runtime())
    assert refused.update["messages"][0].status == "error"
    assert "actual blocked" in refused.update["messages"][0].content
