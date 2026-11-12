"""Engagement execution time window.

Runs are allowed only inside the RoE's ``authorized_windows`` and never inside
its ``blackout_windows`` (``plan/roe.json:machine_enforcement``). The window is
checked at the very start of a run (``before_agent``) and again before every
model call (``before_model``). Outside the window the run ends immediately via
``jump_to: "end"``: no model call or tool call happens after the window
closes. A tool call that is already executing finishes, then the run stops at
the next model turn.

Time windows cannot be expressed as an OpenShell network policy, so this is
their only enforcement point. Network scope is enforced by the OpenShell
policy alone.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage

from decepticon.middleware._roe_rules import load_machine_enforcement
from decepticon.middleware.engagement import EngagementContextState
from decepticon_core.types.roe import evaluate_time_window

#: ``AIMessage.additional_kwargs`` key carrying the stop reason for clients.
STOP_KEY = "decepticon_time_window_stop"


class TimeWindowMiddleware(AgentMiddleware):
    """End the run when the current time is outside the execution window."""

    # Same schema as EngagementContextMiddleware so ``workspace_path`` is a
    # declared channel even when this middleware is used on its own. In the
    # agent stack it runs after ENGAGEMENT_CONTEXT, which hydrates the value.
    state_schema = EngagementContextState

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _check(self, state: Any, phase: str) -> dict[str, Any] | None:
        getter = getattr(state, "get", None)
        workspace = getter("workspace_path") if callable(getter) else None
        rules = load_machine_enforcement(workspace)
        if not rules.configured:
            return None
        decision = evaluate_time_window(self._now(), rules)
        if decision.allow:
            return None
        when = "before it started" if phase == "start" else "during execution"
        text = (
            f"[TIME_WINDOW] Run stopped {when}: {decision.reason_code}. "
            f"{decision.reason_detail}. Resume the engagement inside its "
            "authorized time window."
        )
        return {
            "jump_to": "end",
            "messages": [
                AIMessage(
                    content=text,
                    additional_kwargs={
                        STOP_KEY: {
                            "phase": phase,
                            "code": decision.reason_code,
                            "detail": decision.reason_detail,
                        }
                    },
                )
            ],
        }

    @hook_config(can_jump_to=["end"])
    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state, "start")

    @hook_config(can_jump_to=["end"])
    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state, "start")

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state, "running")

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self._check(state, "running")
