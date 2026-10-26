"""MCP tool registration — engagement lifecycle (start / status / findings / cancel)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from decepticon.roe_materialization import engagement_workspace, materialize_engagement_roe


def _bluecell():
    """Resolve the blue engagement-configuration module (optional dependency).

    The module moved to the AutoBot package (migration stage 2c). Without it
    installed the MCP blue block fails with a clean install hint at tool-call
    time instead of breaking the MCP server import — Blue is optional.
    """
    try:
        from autobot.bluecell import engagement_config as bc
    except ImportError as exc:  # pragma: no cover - install-shape dependent
        raise RuntimeError(
            "Blue engagement configuration requires the AutoBot package. "
            "Install it with: pip install decepticon[blue]  (or install "
            "autobot from the BitterSecurity/AutoBot repository)"
        ) from exc
    return bc


from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.engagements import EngagementClient
from decepticon.mcp_server.findings import (
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
from decepticon_core.utils.engagement_scope import is_valid_engagement_label


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
        out_of_scope: list[str] | None = None,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        workspace_path: str | None = None,
        blue_target: str | None = None,
        blue_logs: str | None = None,
    ) -> StartResult:
        """Start an AUTHORIZED Decepticon engagement against one or more targets.

        ``targets`` are URLs, hostnames/CIDRs, repo URLs, or filesystem paths.
        Put scope and rules of engagement in ``instruction`` (what is in and out
        of scope) — the orchestrator gates every tool call against it. Only test
        assets you are authorized to test.

        ``workspace_path`` optionally binds the run to a specific engagement
        workspace (default: the container's ``/workspace``). When the operator
        selected an engagement in the interactive CLI, pass the same binding so
        the thread and the CLI share one workspace. A non-default binding must
        be the engagement's own directory — its final segment has to equal
        ``engagement_name`` (e.g. ``/workspace/<engagement_name>``) — so one
        engagement's run can never write into another engagement's workspace.

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
        # Generate the enforced RoE from the creation scope before any run is
        # dispatched. No derivable in-scope target → fail closed (no engagement).
        try:
            materialize_engagement_roe(name, targets, out_of_scope)
        except ValueError as exc:
            raise ValueError(
                f"cannot start engagement {name!r}: {exc}. Provide at least one "
                "in-scope target; engagements run only against an authorized scope."
            ) from exc
        if blue_target is not None:
            # Blue observation is optional — the engagement runs without it.
            # A broken blue configuration still fails the start, so a reviewed
            # blue.json is never silently skipped.
            try:
                materialize_blue_config(
                    name,
                    build_local_target_config(
                        blue_target,
                        logs=[blue_logs] if blue_logs else None,
                    ),
                )
            except (ValueError, OSError) as exc:
                raise ValueError(
                    f"cannot materialize blue configuration for {name!r}: {exc}"
                ) from exc
        bound_workspace = workspace_path or "/workspace"
        if "\x00" in bound_workspace or ".." in Path(bound_workspace).parts:
            raise ValueError(
                f"invalid workspace_path {bound_workspace!r}; must not contain "
                "path traversal segments"
            )
        return await engagements.start(
            targets=targets,
            instruction=instruction,
            scan_mode=scan_mode,
            engagement_name=name,
            assistant=assistant or config.default_assistant,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            workspace_path=bound_workspace,
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
