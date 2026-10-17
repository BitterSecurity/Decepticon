"""Exercise workflow proof gates through actual registered OPPLAN/task tools."""

from pathlib import Path

import pytest
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from decepticon.middleware.opplan_dispatch import planned_task_tool
from decepticon.workflows import WorkflowSnapshot
from decepticon_core.types.engagement import ObjectiveStatus

from .opplan_helpers import (
    envelope,
    objective,
    state_for,
    workflow,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_child_uses_parent_checkpoint_after_catalog_changes(
    tmp_path: Path, asynchronous: bool
) -> None:
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from langchain_core.messages import HumanMessage

    from decepticon.middleware.workflow import WorkflowMiddleware
    from decepticon.workflows import WorkflowCatalog

    original = workflow()
    changed = WorkflowSnapshot.from_source(
        original.source.replace("Proof-gated research", "Changed catalog")
    )
    child = create_agent(
        FakeListChatModel(responses=["done"]),
        middleware=[WorkflowMiddleware(role="reverser", catalog=WorkflowCatalog([changed]))],
    )
    observed: list[str] = []

    def dispatch(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        result = child.invoke({"messages": [HumanMessage(content=description)]})
        observed.append(result["workflow_snapshot"])
        return "ran"

    async def adispatch(description: str, subagent_type: str, runtime: ToolRuntime) -> str:
        result = await child.ainvoke({"messages": [HumanMessage(content=description)]})
        observed.append(result["workflow_snapshot"])
        return "ran"

    tool = planned_task_tool(
        StructuredTool.from_function(
            name="task", func=dispatch, coroutine=adispatch, description="dispatch"
        )
    )
    target = objective("target", "analyze", "reverser", ObjectiveStatus.PENDING)
    call = envelope(state_for([target]), target.id, target.owner)
    if asynchronous:
        await tool.ainvoke(call)
    else:
        tool.invoke(call)
    assert observed == [original.model_dump_json()]
