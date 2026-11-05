"""Step budget enforcement — the hard stop must strip the tool binding.

Advisory-only wrap-up notices were ignored by smaller models (issue #1145):
the run kept tool-calling until LangGraph's GraphRecursionError discarded the
run. At the cap the middleware must remove the tools from the model call so
the loop ends with a delivered final answer instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from decepticon.middleware.step_budget import StepBudgetMiddleware


class FakeRequest:
    """Minimal ModelRequest stand-in: override records the applied kwargs."""

    def __init__(self, messages: list[Any], tools: list[Any] | None = None) -> None:
        self.state: dict[str, Any] = {"messages": messages}
        self.messages = messages
        self.tools: list[Any] = tools if tools is not None else [object()]
        self.tool_choice: Any = None

    def override(self, **kwargs: Any) -> "FakeRequest":
        clone = FakeRequest(
            kwargs.get("messages", self.messages),
            kwargs.get("tools", self.tools),
        )
        clone.tool_choice = kwargs.get("tool_choice", self.tool_choice)
        return clone


def _messages(n: int) -> list[AIMessage]:
    return [AIMessage(content=f"m{i}") for i in range(n)]


async def _async_identity(request: FakeRequest) -> FakeRequest:
    return request


def test_hard_stop_strips_tools_sync():
    mw = StepBudgetMiddleware(total_steps=100)
    request = FakeRequest(_messages(100))
    handled = mw.wrap_model_call(request, lambda r: r)
    assert handled.tools == []
    assert handled.tool_choice == "none"
    notes = [
        m for m in handled.messages if isinstance(m, SystemMessage) and "HARD STOP" in m.content
    ]
    assert len(notes) == 1


async def test_hard_stop_strips_tools_async():
    mw = StepBudgetMiddleware(total_steps=100)
    request = FakeRequest(_messages(100))
    handled = await mw.awrap_model_call(request, _async_identity)
    assert handled.tools == []
    assert handled.tool_choice == "none"


def test_warn_phase_keeps_tools():
    mw = StepBudgetMiddleware(total_steps=100)
    request = FakeRequest(_messages(85))
    handled = mw.wrap_model_call(request, lambda r: r)
    assert handled.tools == request.tools
    assert handled.tool_choice is None
    notes = [
        m for m in handled.messages if isinstance(m, SystemMessage) and "step budget" in m.content
    ]
    assert len(notes) == 1 and "85 of 100" in notes[0].content


def test_silent_well_under_budget():
    mw = StepBudgetMiddleware(total_steps=100)
    request = FakeRequest(_messages(10))
    handled = mw.wrap_model_call(request, lambda r: r)
    notes = [
        m for m in handled.messages if isinstance(m, SystemMessage) and "step budget" in m.content
    ]
    assert notes == []


def test_invalid_total_rejected():
    with pytest.raises(ValueError):
        StepBudgetMiddleware(total_steps=0)


def test_effective_recursion_limit_adds_margin():
    """The langgraph cap must sit ABOVE the enforced wrap-up point.

    At the budget the middleware strips the tool binding and the model needs
    a few more supersteps to deliver its final answer; a cap equal to the
    budget fires GraphRecursionError in the same superstep and discards the
    run (observed live at a 2000/2000 budget).
    """
    from decepticon.middleware.step_budget import RECURSION_MARGIN, effective_recursion_limit

    assert RECURSION_MARGIN == 50
    assert effective_recursion_limit(2000) == 2050
    assert effective_recursion_limit(25) == 75
    assert effective_recursion_limit(0) == RECURSION_MARGIN
