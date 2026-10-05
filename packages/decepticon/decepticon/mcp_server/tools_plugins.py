from __future__ import annotations

import re
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from decepticon.mcp_server.plugins import PluginClient


def _bundle_name(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError("bundle name must be 1-64 lowercase letters, digits, _ or -")
    return name


def register_plugin_tools(mcp: FastMCP, plugins: PluginClient) -> None:
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    change = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
    destructive = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

    @mcp.tool(annotations=read_only)
    async def decepticon_plugin_bundles() -> dict[str, Any]:
        """List available agent plugin bundles and their live enabled state."""
        return await plugins.request("GET", "")

    @mcp.tool(annotations=change)
    async def decepticon_plugin_enable(name: str) -> dict[str, Any]:
        """Enable a named agent plugin bundle for this running server session."""
        return await plugins.request("POST", f"/{_bundle_name(name)}/enable")

    @mcp.tool(annotations=destructive)
    async def decepticon_plugin_disable(name: str) -> dict[str, Any]:
        """Disable a named optional agent plugin bundle for this server session."""
        if _bundle_name(name) == "standard":
            raise ValueError("the standard bundle cannot be disabled")
        return await plugins.request("POST", f"/{name}/disable")
