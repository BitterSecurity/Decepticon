"""Resource-awareness middleware — the agent knows its step budget.

LangGraph enforces ``recursion_limit`` by raising GraphRecursionError at the
hard cap, which reads to the operator as "Recursion limit of N reached" and
discards the run's momentum. This middleware makes the budget visible to the
MODEL and, past the cap, ENFORCED: near the cap it injects a wrap-up reminder,
and at the cap it strips the tool binding from the model call so the run
terminates with a final answer instead of dying on GraphRecursionError.

Enforcement matters because the advisory notice alone is a suggestion —
models that ignore it (observed on smaller/flash models, issue #1145) keep
tool-calling or re-reporting until LangGraph kills the run and discards
everything collected. Stripping ``tools`` is the graceful sibling of the
runtime cap: the loop ends by the model delivering its result.

The step estimate approximates graph supersteps from the message count
(one model round ≈ one message per step in a linear ReAct loop). It is an
approximation by design — cheap, monotonic, and directionally correct; the
hard cap stays with the runtime.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage
from typing_extensions import override

# The LangGraph ``recursion_limit`` config must sit ABOVE the step budget:
# at the budget the middleware strips the tool binding, and the model then
# needs a few more supersteps to emit its final answer. If the hard cap
# equals the budget, both fire in the same superstep and the run dies on
# GraphRecursionError before the answer lands (observed live on the
# verification stack at a 2000/2000 budget). The margin is runway for the
# wrap-up, not extra work: tools are already gone.
RECURSION_MARGIN = 50


def effective_recursion_limit(budget: int) -> int:
    """LangGraph recursion_limit for a given step budget (budget + margin)."""
    return budget + RECURSION_MARGIN


class StepBudgetMiddleware(AgentMiddleware):
    """Inject wrap-up guidance as the run approaches its step budget."""

    def __init__(self, *, total_steps: int, warn_at_pct: float = 0.8) -> None:
        super().__init__()
        if total_steps <= 0:
            raise ValueError("total_steps must be positive")
        self._total = total_steps
        self._warn_at = warn_at_pct

    @override
    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        note, hard_stop = self._budget_state(request)
        if note is None:
            return handler(request)
        if hard_stop:
            return handler(self._final_answer_only(request, note))
        return handler(request.override(messages=[*request.messages, SystemMessage(content=note)]))

    @override
    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        note, hard_stop = self._budget_state(request)
        if note is None:
            return await handler(request)
        if hard_stop:
            return await handler(self._final_answer_only(request, note))
        return await handler(
            request.override(messages=[*request.messages, SystemMessage(content=note)])
        )

    def _final_answer_only(self, request: Any, note: str) -> Any:
        """Strip the tool binding so the next model call cannot continue the
        tool loop and must deliver a final text answer."""
        return request.override(
            messages=[*request.messages, SystemMessage(content=note)],
            tools=[],
            tool_choice="none",
        )

    def _budget_state(self, request: Any) -> tuple[str | None, bool]:
        """Return ``(advisory note, hard stop reached)`` for this model call."""
        messages = getattr(request, "state", {}).get("messages") or []
        steps = len(messages)
        if steps >= self._total:
            return (
                f"[step budget] HARD STOP REACHED — {steps} of {self._total} steps. "
                "Do not call any more tools. Return your final answer now with the "
                "findings and uncertainty collected so far.",
                True,
            )
        if steps >= self._total * self._warn_at:
            return (
                f"[step budget] {steps} of {self._total} steps used — approaching the "
                "hard stop. Conclude the current line of work and move to delivering "
                "your result.",
                False,
            )
        return (None, False)
