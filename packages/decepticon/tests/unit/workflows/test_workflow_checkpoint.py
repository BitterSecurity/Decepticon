"""Workflow boundaries and real LangChain/checkpointer execution."""

from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from decepticon.middleware.workflow import WorkflowMiddleware, WorkflowSelection, resolve_workflow
from decepticon.workflows import WorkflowCatalog, WorkflowError, WorkflowSnapshot

from .runtime_helpers import source


def test_real_agent_persists_selection_and_rejects_resume_switch(tmp_path: Path) -> None:
    # Given
    pins = [WorkflowSnapshot.from_source(source(name)) for name in ["red-team", "pentest"]]
    graph = create_agent(
        FakeListChatModel(responses=["done"]),
        middleware=[WorkflowMiddleware(role="recon", catalog=WorkflowCatalog(pins))],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "workflow-test", "workflow_id": "pentest"}}
    # When
    result = graph.invoke({"messages": [("user", "inspect")]}, config)
    # Then
    assert WorkflowSnapshot.model_validate_json(result["workflow_snapshot"]).id == "pentest"
    with pytest.raises(WorkflowError):
        graph.invoke(
            {"messages": [("user", "resume")]},
            {"configurable": {"thread_id": "workflow-test", "workflow_id": "red-team"}},
        )


@pytest.mark.asyncio
async def test_real_async_agent_restores_pin_without_selection() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source("pentest"))
    graph = create_agent(
        FakeListChatModel(responses=["done"]),
        middleware=[WorkflowMiddleware(role="recon", catalog=WorkflowCatalog([pin]))],
        checkpointer=InMemorySaver(),
    )
    await graph.ainvoke(
        {"messages": [("user", "inspect")]},
        {"configurable": {"thread_id": "async-pin", "workflow_id": "pentest"}},
    )
    # When
    result = await graph.ainvoke(
        {"messages": [("user", "continue")]},
        {"configurable": {"thread_id": "async-pin"}},
    )
    # Then
    assert WorkflowSnapshot.model_validate_json(result["workflow_snapshot"]).sha256 == pin.sha256


def test_real_model_receives_role_scoped_contract() -> None:
    # Given
    import json

    from langchain.agents.middleware import AgentMiddleware
    from langchain.agents.middleware.types import ModelRequest

    observed: list[str] = []

    class CaptureRequest(AgentMiddleware):
        def wrap_model_call(self, request: ModelRequest, handler):
            assert request.system_message is not None
            observed.append(request.system_message.text)
            return handler(request)

    pin = WorkflowSnapshot.from_source(
        source(stages="[{id: discover, specialist: recon}, {id: act, specialist: exploit}]")
    )
    graph = create_agent(
        FakeListChatModel(responses=["done"]),
        system_prompt="base",
        middleware=[
            WorkflowMiddleware(role="recon", catalog=WorkflowCatalog([pin])),
            CaptureRequest(),
        ],
    )
    # When
    graph.invoke({"messages": [("user", "inspect")]})
    # Then
    contract = json.loads(
        observed[0].split("<workflow-contract>")[1].split("</workflow-contract>")[0]
    )
    assert contract["id"] == "red-team"
    assert [stage["id"] for stage in contract["stages"]] == ["discover"]


def test_shared_graph_selects_workflows_per_thread() -> None:
    # Given
    pins = [WorkflowSnapshot.from_source(source(name)) for name in ["red-team", "pentest"]]
    graph = create_agent(
        FakeListChatModel(responses=["done"]),
        middleware=[WorkflowMiddleware(role="recon", catalog=WorkflowCatalog(pins))],
        checkpointer=InMemorySaver(),
    )
    # When
    results = [
        graph.invoke(
            {"messages": [("user", "inspect")]},
            {"configurable": {"thread_id": pin.id, "workflow_id": pin.id}},
        )
        for pin in pins
    ]
    # Then
    assert [
        WorkflowSnapshot.model_validate_json(row["workflow_snapshot"]).id for row in results
    ] == ["red-team", "pentest"]


def test_graph_default_selects_fresh_workflow() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source("pentest"))
    # When
    selected = resolve_workflow(
        WorkflowCatalog([pin]), WorkflowSelection(workflow_default_id="pentest"), None
    )
    # Then
    assert selected.id == "pentest"


def test_checkpoint_pin_takes_precedence_over_graph_default() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source("pentest"))
    # When
    selected = resolve_workflow(
        WorkflowCatalog([]),
        WorkflowSelection(workflow_default_id="bug-bounty"),
        pin.model_dump_json(),
    )
    # Then
    assert selected.sha256 == pin.sha256


def test_explicit_selection_takes_precedence_over_graph_default() -> None:
    # Given
    pin = WorkflowSnapshot.from_source(source("pentest"))
    # When
    selected = resolve_workflow(
        WorkflowCatalog([pin]),
        WorkflowSelection(workflow_id="pentest", workflow_default_id="bug-bounty"),
        None,
    )
    # Then
    assert selected.id == "pentest"


def test_graph_input_cannot_override_server_selected_pin() -> None:
    trusted = WorkflowSnapshot.from_source(source(body="trusted"))
    forged = WorkflowSnapshot.from_source(source(body="forged"))
    graph = create_agent(
        FakeListChatModel(responses=["done"]),
        middleware=[WorkflowMiddleware(role="recon", catalog=WorkflowCatalog([trusted]))],
    )
    result = graph.invoke(
        {"messages": [("user", "inspect")], "workflow_snapshot": forged.model_dump_json()}
    )
    assert result["workflow_snapshot"] == trusted.model_dump_json()
    assert "workflow_snapshot" not in graph.get_input_jsonschema()["properties"]


@pytest.mark.parametrize("legacy", [False, True])
def test_only_protected_old_checkpoint_gets_legacy_policy(legacy: bool) -> None:
    from langchain_core.messages import HumanMessage

    from decepticon.middleware.opplan import OPPLANMiddleware

    class ToolModel(FakeListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    pin = WorkflowSnapshot.from_source(source(stages="[{id: discover, specialist: recon}]"))
    graph = create_agent(
        ToolModel(responses=["done"]),
        middleware=[
            WorkflowMiddleware(role="recon", catalog=WorkflowCatalog([pin])),
            OPPLANMiddleware(),
        ],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "old" if legacy else "fresh"}}
    objectives = [
        {
            "id": identifier,
            "status": status,
            "phase": "recon",
            "title": identifier,
            "description": "legacy work",
            "acceptance_criteria": ["proof"],
            "priority": 1,
        }
        for identifier, status in [("done", "completed"), ("active", "in-progress")]
    ]
    if legacy:
        graph.update_state(
            config,
            {"objectives": objectives, "plan_revision": 2},
            as_node="model",
        )
        assert graph.get_state(config).values["objectives"] == objectives
    result = graph.invoke(
        {"messages": [HumanMessage(content="continue")], "objectives": objectives}, config
    )
    selected = WorkflowSnapshot.model_validate_json(result["workflow_snapshot"])
    assert selected.version == ("1-legacy" if legacy else "1")
    assert bool(selected.document.metadata.stages) is not legacy
    if legacy:
        assert result["objectives"] == objectives
        resumed = graph.invoke({"messages": [HumanMessage(content="continue again")]}, config)
        assert resumed["workflow_snapshot"] == result["workflow_snapshot"]
    else:
        assert result.get("objectives", []) == []
