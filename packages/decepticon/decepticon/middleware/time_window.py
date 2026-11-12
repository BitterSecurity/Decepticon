"""Engagement execution time window with pause and automatic resume.

Runs are allowed only inside the RoE's ``authorized_windows`` and never inside
its ``blackout_windows`` (``plan/roe.json:machine_enforcement``). The window is
checked at the very start of a run (``before_agent``) and again before every
model call (``before_model``).

Outside the window the run is **paused** with a LangGraph ``interrupt`` whose
payload names the next time the window allows work (``resume_at``). Resuming
the thread (``Command(resume=...)``, sent by ``decepticon-cli
time-window-resume`` once ``resume_at`` has passed) re-enters the same hook:
if the window is open the run continues exactly where it stopped; if not, it
pauses again. A tool call that is already executing finishes first.

Without a checkpointer a paused run could never be resumed, so the run ends
instead (``jump_to: "end"``) with the same stop details on its final message.

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
from decepticon_core.types.roe import MachineEnforcement, evaluate_time_window

#: ``AIMessage.additional_kwargs`` key carrying the stop reason (stop mode).
STOP_KEY = "decepticon_time_window_stop"
#: ``interrupt`` payload ``kind`` used by the resumer to find paused runs.
PAUSE_KIND = "decepticon.time_window"


def next_allowed_at(now: datetime, rules: MachineEnforcement) -> datetime | None:
    """Earliest instant >= ``now`` at which the time window allows work.

    Candidates are ``now``, the start of every future authorized window and
    the end of every blackout window; the first one the window evaluation
    allows wins. ``None`` when no future instant is allowed.
    """
    candidates = {now}
    candidates.update(start for start, _ in rules.authorized_windows if start > now)
    candidates.update(end for _, end in rules.blackout_windows if end > now)
    for moment in sorted(candidates):
        if evaluate_time_window(moment, rules).allow:
            return moment
    return None


def _checkpointer_available() -> bool:
    try:
        from langgraph.config import get_config
    except ImportError:
        return False
    try:
        configurable = get_config().get("configurable") or {}
    except RuntimeError:
        return False
    return configurable.get("__pregel_checkpointer") is not None


class TimeWindowMiddleware(AgentMiddleware):
    """Pause (or, without a checkpointer, end) runs outside the window."""

    # Same schema as EngagementContextMiddleware so ``workspace_path`` is a
    # declared channel even when this middleware is used on its own. In the
    # agent stack it runs after ENGAGEMENT_CONTEXT, which hydrates the value.
    state_schema = EngagementContextState

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        pause: bool | None = None,
    ) -> None:
        super().__init__()
        self._now = now or (lambda: datetime.now(timezone.utc))
        # None = pause when a checkpointer is available, else end the run.
        self._pause = pause

    def _check(self, state: Any, phase: str) -> dict[str, Any] | None:
        getter = getattr(state, "get", None)
        workspace = getter("workspace_path") if callable(getter) else None
        rules = load_machine_enforcement(workspace)
        if not rules.configured:
            return None
        while True:
            now = self._now()
            decision = evaluate_time_window(now, rules)
            if decision.allow:
                return None
            resume_at = next_allowed_at(now, rules)
            details = {
                "kind": PAUSE_KIND,
                "phase": phase,
                "code": decision.reason_code,
                "detail": decision.reason_detail,
                "resume_at": resume_at.isoformat() if resume_at else None,
                "workspace_path": workspace,
            }
            pause = self._pause if self._pause is not None else _checkpointer_available()
            if not pause or resume_at is None:
                return self._end(details)
            from langgraph.types import interrupt

            # Resuming re-enters this loop and re-checks the window.
            interrupt(details)

    @staticmethod
    def _end(details: dict[str, Any]) -> dict[str, Any]:
        when = "before it started" if details["phase"] == "start" else "during execution"
        resume = (
            f" The window next opens at {details['resume_at']}."
            if details.get("resume_at")
            else " No future authorized window is configured."
        )
        text = f"[TIME_WINDOW] Run stopped {when}: {details['code']}. {details['detail']}.{resume}"
        return {
            "jump_to": "end",
            "messages": [AIMessage(content=text, additional_kwargs={STOP_KEY: details})],
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
