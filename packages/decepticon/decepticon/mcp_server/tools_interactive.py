"""MCP tool registration — interactive engagement control (list / steer / watch).

These tools make the MCP surface feel like the Decepticon CLI from a phone:
browse and resume engagements, send operator messages to steer a run, read the
transcript to watch progress, inspect OPPLAN/scope, and tail the live stream.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from decepticon.mcp_server.conversation import build_engagement_state, build_transcript
from decepticon.mcp_server.engagements import EngagementClient
from decepticon.mcp_server.findings import engagement_workspace
from decepticon.mcp_server.models import (
    EngagementState,
    EngagementSummary,
    RunHandle,
    Transcript,
    WatchResult,
)
from decepticon.mcp_server.tools_lifecycle import require_red_approval, selected_workspace

_ACTIVE = ("pending", "running")


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


async def _run_status(engagements: EngagementClient, thread_id: str) -> tuple[str | None, str]:
    latest = await engagements.latest_run(thread_id)
    if latest is None:
        return None, "none"
    return str(latest.get("run_id")), str(latest.get("status", "unknown"))


def register_interactive_tools(mcp: FastMCP, engagements: EngagementClient) -> None:
    """Register list / send / transcript / state / watch tools on the server."""

    @mcp.tool()
    async def decepticon_list_engagements(limit: int = 20) -> list[EngagementSummary]:
        """List recent engagements (most recent first) to browse or resume.

        Use a returned ``thread_id`` as the handle for ``decepticon_transcript``,
        ``decepticon_send_message``, and the other engagement tools.
        """
        return await engagements.list_engagements(limit=_clamp(limit, 1, 100))

    @mcp.tool()
    async def decepticon_send_message(
        thread_id: str, message: str, assistant: str | None = None
    ) -> RunHandle:
        """Send an operator message into a running/standing engagement.

        Use to steer focus ("ignore the staging host, dig into the API"), answer
        the coordinator, or switch models mid-engagement with ``/model <id>``.
        The turn is enqueued after any active run, so it is never rejected.
        """
        selected = os.environ.get("DECEPTICON_ENGAGEMENT", "")
        if selected:
            state = await engagements.get_state(thread_id)
            values = state.get("values") if isinstance(state, dict) else None
            if not isinstance(values, dict) or (
                values.get("engagement_name") != selected
                or values.get("workspace_path") != str(engagement_workspace(selected))
            ):
                raise ValueError("thread does not belong to the selected engagement workspace")
            resolved = assistant
            if resolved is None:
                latest = await engagements.latest_run(thread_id)
                resolved = str(latest.get("assistant_id")) if latest else None
            if resolved != "soundwave":
                require_red_approval(selected_workspace(selected))
        return await engagements.send_message(
            thread_id=thread_id, message=message, assistant=assistant
        )

    @mcp.tool()
    async def decepticon_transcript(
        thread_id: str, after_index: int = 0, limit: int = 40
    ) -> Transcript:
        """Read the engagement transcript — the persisted narrative to watch it.

        Returns orchestrator messages (operator prompts, coordinator replies, and
        ``task()`` delegations to specialists) from ``after_index`` onward. Poll
        with the returned ``next_index`` to stream progress incrementally.
        """
        _, status = await _run_status(engagements, thread_id)
        state = await engagements.get_state(thread_id)
        return build_transcript(
            state,
            thread_id=thread_id,
            run_status=status,
            after_index=after_index,
            limit=_clamp(limit, 1, 200),
        )

    @mcp.tool()
    async def decepticon_engagement_state(thread_id: str) -> EngagementState:
        """Inspect engagement context: OPPLAN / objectives / scope / phase.

        Returns the orchestrator's working state (everything except the message
        log) plus message count and run status.
        """
        _, status = await _run_status(engagements, thread_id)
        state = await engagements.get_state(thread_id)
        return build_engagement_state(state, thread_id=thread_id, run_status=status)

    @mcp.tool()
    async def decepticon_watch(
        thread_id: str, max_seconds: int = 20, max_events: int = 40
    ) -> WatchResult:
        """Tail the live run stream for a few seconds (sub-agent activity feed).

        Collects up to ``max_events`` events or until ``max_seconds`` elapses,
        then returns. Call again to keep watching. Returns no events when no run
        is active (the engagement is idle or finished).
        """
        run_id, status = await _run_status(engagements, thread_id)
        if run_id is None or status not in _ACTIVE:
            return WatchResult(
                thread_id=thread_id,
                run_id=run_id,
                run_status=status,
                events=[],
                truncated=False,
            )
        events, truncated = await engagements.watch(
            thread_id=thread_id,
            run_id=run_id,
            max_seconds=float(_clamp(max_seconds, 1, 45)),
            max_events=_clamp(max_events, 1, 100),
        )
        return WatchResult(
            thread_id=thread_id,
            run_id=run_id,
            run_status=status,
            events=events,
            truncated=truncated,
        )
