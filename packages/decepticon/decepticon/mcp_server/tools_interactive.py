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
from decepticon.mcp_server.models import (
    EngagementState,
    EngagementSummary,
    RunHandle,
    Transcript,
    WatchResult,
)
from decepticon.mcp_server.tools_lifecycle import selected_thread_state

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
        bounded = _clamp(limit, 1, 100)
        selected = os.environ.get("DECEPTICON_ENGAGEMENT", "")
        rows = await engagements.list_engagements(limit=100 if selected else bounded)
        if not selected:
            return rows
        visible: list[EngagementSummary] = []
        for row in rows:
            if row.engagement_name != selected:
                continue
            try:
                await selected_thread_state(engagements, row.thread_id)
            except ValueError:
                continue
            visible.append(row)
            if len(visible) == bounded:
                break
        return visible

    @mcp.tool()
    async def decepticon_send_message(
        thread_id: str, message: str, assistant: str | None = None
    ) -> RunHandle:
        """Send an operator message into a running/standing engagement.

        Use to steer focus ("ignore the staging host, dig into the API"), answer
        the coordinator, or switch models mid-engagement with ``/model <id>``.
        The turn is enqueued after any active run, so it is never rejected.
        """
        if os.environ.get("DECEPTICON_ENGAGEMENT"):
            await selected_thread_state(engagements, thread_id)
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
        state = await selected_thread_state(engagements, thread_id)
        _, status = await _run_status(engagements, thread_id)
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
        state = await selected_thread_state(engagements, thread_id)
        _, status = await _run_status(engagements, thread_id)
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
        if os.environ.get("DECEPTICON_ENGAGEMENT"):
            await selected_thread_state(engagements, thread_id)
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
