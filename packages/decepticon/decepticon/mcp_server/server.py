"""FastMCP server exposing Decepticon engagement control to external agents.

This is the seam that makes external agent runtimes (OpenClaw, Hermes, or any
MCP client) able to *drive* Decepticon like its CLI — from a phone, via those
agents' chat channels. The full surface:

- ``decepticon_list_graphs`` / ``decepticon_list_engagements`` — discover & resume
- ``decepticon_start_engagement`` — launch an authorized engagement
- ``decepticon_send_message`` — steer / answer / ``/model`` switch mid-run
- ``decepticon_transcript`` — watch the orchestrator narrative incrementally
- ``decepticon_watch`` — tail the live sub-agent stream for a few seconds
- ``decepticon_engagement_state`` — inspect OPPLAN / scope / phase
- ``decepticon_engagement_status`` / ``decepticon_engagement_findings`` — status & SARIF
- ``decepticon_cancel_engagement`` — stop a run

The server is a thin control plane. The red-team work runs inside the
Decepticon LangGraph server (RoE enforcement, sandbox, knowledge-graph
persistence); this module only translates MCP tool calls into LangGraph runs
and reads persisted state/findings back.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from decepticon.mcp_server.auth import build_auth
from decepticon.mcp_server.blue import BlueClient
from decepticon.mcp_server.config import ServerConfig, load_config
from decepticon.mcp_server.engagements import EngagementClient
from decepticon.mcp_server.tools_blue import register_blue_tools
from decepticon.mcp_server.tools_interactive import register_interactive_tools
from decepticon.mcp_server.tools_lifecycle import register_lifecycle_tools


def build_server(
    config: ServerConfig | None = None,
    *,
    client: Any | None = None,
    blue_client: BlueClient | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> FastMCP:
    """Construct the Decepticon engagement MCP server with all tools registered.

    ``client`` injects a pre-built LangGraph SDK client (tests/embedding);
    production callers leave it ``None`` so it is created from ``config``.
    """
    cfg = config or load_config()
    engagements = EngagementClient(cfg, client=client)
    verifier, auth = build_auth(cfg, host=host, port=port)
    mcp = FastMCP("decepticon", host=host, port=port, token_verifier=verifier, auth=auth)
    register_lifecycle_tools(mcp, engagements, cfg)
    register_interactive_tools(mcp, engagements)
    register_blue_tools(mcp, blue_client or BlueClient())
    return mcp
