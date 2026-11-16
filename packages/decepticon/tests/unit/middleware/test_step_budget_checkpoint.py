"""Exercise real LangChain/Graph checkpoint and interrupt behavior offline."""

from typing import Any

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt

from decepticon.middleware.step_budget import StepBudgetMiddleware, effective_recursion_limit


class ToolLoopModel(BaseChatModel):
    """Always request another tool when bound; respect actual binding removal."""

    @property
    def _llm_type(self) -> str:
        return "offline-step-budget-fixture"

    def bind_tools(self, tools, **kwargs: Any):
        return self.bind(fixture_tools_enabled=bool(tools))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        message = (
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "work",
                        "args": {},
                        "id": f"work-{len(messages)}",
                    }
                ],
            )
            if kwargs.get("fixture_tools_enabled")
            else AIMessage(content="finished")
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


@tool
def work() -> str:
    """Return a deterministic local fixture result."""
    return "local fixture"


async def test_checkpoint_followup_can_work_after_the_previous_turn_exhausted_its_budget():
    graph = create_agent(
        ToolLoopModel(),
        tools=[work],
        middleware=[StepBudgetMiddleware(total_steps=8)],
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {"thread_id": "owned-offline-multi-turn"},
        "recursion_limit": effective_recursion_limit(8),
    }
    first = await graph.ainvoke({"messages": [HumanMessage(content="first request")]}, config)
    first_tools = [m for m in first["messages"] if isinstance(m, ToolMessage)]
    assert len(first_tools) == 4
    assert first["messages"][-1].content == "finished"
    second = await graph.ainvoke({"messages": [HumanMessage(content="follow-up")]}, config)
    second_tools = [m for m in second["messages"] if isinstance(m, ToolMessage)]
    assert len(second_tools) == 8
    assert second["messages"][-1].content == "finished"
    assert [m.id for m in second["messages"][: len(first["messages"])]] == [
        m.id for m in first["messages"]
    ]


def test_interrupt_resume_retains_the_same_turn_limit_and_new_input_gets_a_fresh_turn():
    @tool("work")
    def gated_work() -> str:
        """Wait for an explicit fixture approval before returning."""
        assert interrupt("local fixture approval") is True
        return "approved local fixture"

    graph = create_agent(
        ToolLoopModel(),
        tools=[gated_work],
        middleware=[StepBudgetMiddleware(total_steps=6)],
        checkpointer=InMemorySaver(),
    )
    config = {
        "configurable": {"thread_id": "owned-offline-interrupt"},
        "recursion_limit": effective_recursion_limit(6),
    }
    result = graph.invoke({"messages": [HumanMessage(content="bounded work")]}, config)
    resumes = 0
    while result.get("__interrupt__") and resumes < 6:
        result = graph.invoke(Command(resume=True), config)
        resumes += 1
    assert resumes == 3
    assert not result.get("__interrupt__")
    assert len([m for m in result["messages"] if isinstance(m, ToolMessage)]) == 3
    assert result["messages"][-1].content == "finished"
    followup = graph.invoke({"messages": [HumanMessage(content="new work")]}, config)
    assert followup.get("__interrupt__")
