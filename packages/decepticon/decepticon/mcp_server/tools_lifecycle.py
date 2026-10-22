"""MCP tool registration — engagement lifecycle (start / status / findings / cancel)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.engagements import EngagementClient
from decepticon.mcp_server.findings import (
    engagement_workspace,
    load_findings_graph,
    summarize_findings,
)
from decepticon.mcp_server.models import (
    FindingsResult,
    GraphInfo,
    ScanMode,
    StartResult,
    StatusResult,
)
from decepticon_core.contracts.slots import SLOTS_PER_ROLE, MiddlewareSlot
from decepticon_core.registry.roles import RoleRegistry, default_role_purpose
from decepticon_core.utils.engagement_scope import is_valid_engagement_label

# Engagements start through an *orchestration* graph only — the main
# orchestrator (carries the SUBAGENT slot), the planner (soundwave,
# purpose=planning) or the blue observer (blue_cell, purpose=observation).
# Bash specialists (recon, exploit, the operators, …) are execution sub-agents
# the orchestrator reaches via its task() tool; started directly they skip the
# orchestrator's RoE/OPPLAN generation, so RoEGuardrailMiddleware finds no
# plan/roe.json and falls back to audit-only mode (no scope enforcement — see
# middleware/roe.py). This mirrors the CLI's documented contract (see
# clients/cli/src/commands/agent.ts: "Only orchestration agents are surfaced …
# sub-agents … shouldn't be selected directly") on the MCP start path.
_ORCHESTRATION_PURPOSES = frozenset({"planning", "observation"})


def is_engagement_entry_graph(assistant: str) -> bool:
    """True when ``assistant`` is an orchestration graph safe to start directly."""
    role = RoleRegistry.get(assistant)
    purpose = role.purpose if role else default_role_purpose(assistant)
    if purpose in _ORCHESTRATION_PURPOSES:
        return True
    return MiddlewareSlot.SUBAGENT in SLOTS_PER_ROLE.get(assistant, frozenset())


def default_engagement_name() -> str:
    return "mcp-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


async def selected_thread_state(engagements: EngagementClient, thread_id: str) -> dict[str, Any]:
    state = await engagements.get_state(thread_id)
    if not isinstance(state, dict):
        raise ValueError("invalid engagement thread state")
    selected = os.environ.get("DECEPTICON_ENGAGEMENT", "")
    if selected:
        values = state.get("values")
        if not isinstance(values, dict) or (
            values.get("engagement_name") != selected
            or values.get("workspace_path") != str(engagement_workspace(selected))
        ):
            raise ValueError("thread does not belong to the selected engagement workspace")
    return state


def register_lifecycle_tools(
    mcp: FastMCP, engagements: EngagementClient, config: ServerConfig
) -> None:
    """Register start / status / findings / cancel tools on the server."""

    @mcp.tool()
    async def decepticon_list_graphs() -> list[GraphInfo]:
        """List the engagement graphs the connected Decepticon server exposes.

        Pick a graph: ``decepticon`` (full kill-chain engagement), ``recon``
        (reconnaissance only), ``soundwave`` (planning), etc. Requires a running
        Decepticon LangGraph server.
        """
        return await engagements.list_graphs()

    @mcp.tool()
    async def decepticon_start_engagement(
        targets: list[str],
        instruction: str = "",
        scan_mode: ScanMode = "standard",
        engagement_name: str | None = None,
        assistant: str | None = None,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
    ) -> StartResult:
        """Start an AUTHORIZED Decepticon engagement against one or more targets.

        ``targets`` are URLs, hostnames/CIDRs, repo URLs, or filesystem paths.
        Put scope and rules of engagement in ``instruction`` (what is in and out
        of scope) — the orchestrator gates every tool call against it. Only test
        assets you are authorized to test.

        Returns immediately with a ``thread_id`` (the engagement handle). Drive
        everything else with that handle: ``decepticon_transcript`` to watch,
        ``decepticon_send_message`` to steer, ``decepticon_engagement_findings``
        to pull results.
        """
        name = engagement_name or default_engagement_name()
        if not is_valid_engagement_label(name):
            raise ValueError(
                f"invalid engagement_name {name!r}; must match "
                "[A-Za-z0-9][A-Za-z0-9._-]{0,127} (no path separators or '..')"
            )
        resolved_assistant = assistant or config.default_assistant
        if not is_engagement_entry_graph(resolved_assistant):
            raise ValueError(
                f"assistant {resolved_assistant!r} is a specialist sub-agent, not an "
                "engagement orchestrator, so it cannot start an engagement directly. "
                "Use an orchestration graph — decepticon (Red kill-chain), soundwave "
                "(Plan) or blue_cell (Blue) — which reaches specialists through its "
                "task() tool and generates the plan/roe.json the guardrail enforces. "
                "Starting a specialist directly would skip RoE generation and leave "
                "RoEGuardrailMiddleware in audit-only mode (no scope enforcement). "
                "List startable graphs with decepticon_list_graphs."
            )
        return await engagements.start(
            targets=targets,
            instruction=instruction,
            scan_mode=scan_mode,
            engagement_name=name,
            assistant=resolved_assistant,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
        )

    @mcp.tool()
    async def decepticon_engagement_status(
        thread_id: str, engagement_name: str = ""
    ) -> StatusResult:
        """Report an engagement's latest run status + whether findings exist yet.

        ``status`` is the LangGraph run status (``pending``/``running``/
        ``success``/``error``/``timeout``/``interrupted``/``none``). Pass
        ``engagement_name`` to also learn whether findings have been persisted.
        """
        if os.environ.get("DECEPTICON_ENGAGEMENT"):
            await selected_thread_state(engagements, thread_id)
        latest = await engagements.latest_run(thread_id)
        run_id = str(latest["run_id"]) if latest else None
        status = str(latest.get("status", "unknown")) if latest else "none"
        findings_available = (
            bool(engagement_name)
            and is_valid_engagement_label(engagement_name)
            and (engagement_workspace(engagement_name) / "graph.json").exists()
        )
        return StatusResult(
            thread_id=thread_id,
            run_id=run_id,
            status=status,
            findings_available=findings_available,
        )

    @mcp.tool()
    async def decepticon_engagement_findings(
        engagement_name: str, include_sarif: bool = False
    ) -> FindingsResult:
        """Fetch the findings summary for an engagement (optionally full SARIF).

        Returns counts by SARIF level plus, when ``include_sarif`` is true, the
        complete SARIF v2.1.0 document. ``available`` is false until the
        orchestrator persists findings.
        """
        graph = load_findings_graph(engagement_name)
        return summarize_findings(
            graph, engagement_name=engagement_name, include_sarif=include_sarif
        )

    @mcp.tool()
    async def decepticon_cancel_engagement(thread_id: str) -> str:
        """Cancel the active run on an engagement thread."""
        if os.environ.get("DECEPTICON_ENGAGEMENT"):
            await selected_thread_state(engagements, thread_id)
        run_id = await engagements.cancel(thread_id)
        return f"cancelled {run_id}" if run_id else "no active run to cancel"
