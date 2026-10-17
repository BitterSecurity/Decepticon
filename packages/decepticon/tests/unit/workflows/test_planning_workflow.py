import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import InputAgentState, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolNode

from decepticon.middleware.filesystem import FilesystemMiddleware
from decepticon.middleware.workflow import PlanningWorkflowMiddleware, WorkflowMiddleware
from decepticon.workflows import WorkflowCatalog, WorkflowError, WorkflowSnapshot


class Capture(AgentMiddleware[AgentState]):
    def __init__(self, observed: list[str]) -> None:
        self.observed = observed

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        assert request.system_message is not None
        self.observed.append(request.system_message.text)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        assert request.system_message is not None
        self.observed.append(request.system_message.text)
        return await handler(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_plan_pins_its_own_contract_and_resumes_after_catalog_edit(
    asynchronous: bool,
) -> None:
    catalog = WorkflowCatalog.default()
    planning = catalog.resolve("planning")
    execution = catalog.resolve("red-team")
    observed: list[str] = []
    middleware = PlanningWorkflowMiddleware(catalog=catalog)
    graph = create_agent(
        FakeListChatModel(responses=["planning response"]),
        system_prompt="stable planning capability",
        middleware=[middleware, Capture(observed)],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {
        "configurable": {
            "thread_id": "optional-plan",
            "planning_workflow_id": "planning",
            "workflow_id": "red-team",
            "workflow_snapshot": execution.model_dump_json(),
        }
    }
    inputs: InputAgentState = {
        "messages": [HumanMessage(content="Discuss the supplied operation.")]
    }
    result = await graph.ainvoke(inputs, config) if asynchronous else graph.invoke(inputs, config)
    assert result["planning_workflow_snapshot"] == planning.model_dump_json()
    assert "workflow_snapshot" not in result
    assert observed[-1].startswith("stable planning capability")
    contract = json.loads(
        observed[-1].split("<workflow-contract>")[1].split("</workflow-contract>")[0]
    )
    assert contract["id"] == "planning"
    assert contract["purpose"] == "planning"
    assert contract["stages"] == []

    changed = WorkflowSnapshot.from_source(planning.source + "\nChanged catalog procedure.\n")
    middleware.catalog = WorkflowCatalog([changed])
    resumed = await graph.ainvoke(inputs, config) if asynchronous else graph.invoke(inputs, config)
    assert resumed["planning_workflow_snapshot"] == planning.model_dump_json()
    assert "Changed catalog procedure." not in observed[-1]


@pytest.mark.parametrize("planning", [False, True])
def test_planning_and_execution_contracts_cannot_be_substituted(planning: bool) -> None:
    middleware = PlanningWorkflowMiddleware() if planning else WorkflowMiddleware(role="decepticon")
    graph = create_agent(FakeListChatModel(responses=["unexpected"]), middleware=[middleware])
    config: RunnableConfig = {
        "configurable": {
            "planning_workflow_id": "red-team",
            "workflow_id": "planning",
        }
    }
    with pytest.raises(WorkflowError, match="not a .* workflow"):
        graph.invoke({"messages": [HumanMessage(content="Continue.")]}, config)


def test_pinned_planning_contract_cannot_switch_to_a_different_plan() -> None:
    original = WorkflowCatalog.default().resolve("planning")
    alternative = WorkflowSnapshot.from_source(
        original.source.replace("id: planning", "id: alternate-planning", 1)
    )
    middleware = PlanningWorkflowMiddleware(catalog=WorkflowCatalog([original, alternative]))
    graph = create_agent(
        FakeListChatModel(responses=["plan"]),
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "pin"}}
    graph.invoke({"messages": [HumanMessage(content="Plan.")]}, config)
    with pytest.raises(WorkflowError, match="ID differs"):
        graph.invoke(
            {"messages": [HumanMessage(content="Revise.")]},
            {"configurable": {"thread_id": "pin", "planning_workflow_id": "alternate-planning"}},
        )


def test_soundwave_factory_injects_planning_without_opplan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from decepticon.agents.standard import soundwave

    observed: list[str] = []
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda model, *_args, **_kwargs: model)
    layers = [FilesystemMiddleware(backend=backend, plan_only=True), Capture(observed)]
    monkeypatch.setattr(soundwave, "build_middleware", lambda **_kwargs: list(layers))
    graph = soundwave.create_soundwave_agent(
        backend=backend,
        llm=FakeListChatModel(responses=["draft discussion"]),
        fallback_models=[],
        tools=[],
    )
    result = graph.invoke({"messages": [HumanMessage(content="Discuss one planning question.")]})
    assert (
        WorkflowSnapshot.model_validate_json(result["planning_workflow_snapshot"]).id == "planning"
    )
    assert "<workflow-contract>" in observed[-1]
    tool_node = graph.nodes["tools"].bound
    assert isinstance(tool_node, ToolNode)
    assert "commit_opplan" not in tool_node.tools_by_name
    assert "planning_draft_ready" not in str(result["messages"][-1].content)


def test_autohunt_does_not_inherit_the_interactive_planning_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decepticon.agents.standard import autohunt

    observed: list[str] = []
    monkeypatch.setattr(autohunt, "build_middleware", lambda **_kwargs: [Capture(observed)])
    graph = autohunt.create_autohunt_agent(
        backend=FilesystemBackend(root_dir="/tmp", virtual_mode=True),
        llm=FakeListChatModel(responses=["autohunt response"]),
        fallback_models=[],
        tools=[],
    )
    result = graph.invoke({"messages": [HumanMessage(content="Review supplied context.")]})
    assert "planning_workflow_snapshot" not in result
    assert "<workflow-contract>" not in observed[-1]
