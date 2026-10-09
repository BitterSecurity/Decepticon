"""Generic MCP client registry — dynamic discovery and dispatch.

Instead of fixed MCP mounts (e.g. Ghidra MCP), this registry loads MCP
server configs from ~/.decepticon/mcp-servers.json and provides tools for
agents to discover and call tools on any connected server dynamically.

Tools are NOT pre-registered in the system prompt (zero context cost).
Agents discover them on demand via list_mcps/search_mcp_tools/call_mcp.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


MCP_CONFIG_PATHS = [
    Path.home() / ".decepticon" / "mcp-servers.json",
    Path("/etc/decepticon/mcp-servers.json"),
]

_registry: dict[str, dict] = {}
_lock = threading.Lock()
_loaded = False


def _load_config() -> list[dict]:
    for p in MCP_CONFIG_PATHS:
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                if isinstance(data, list):
                    return data
            except (OSError, json.JSONDecodeError):
                continue
    return []


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        servers = _load_config()
        for server in servers:
            name = server.get("name", "")
            if not name:
                continue
            _registry[name] = {
                "name": name,
                "transport": server.get("transport", "stdio"),
                "url": server.get("url", ""),
                "description": server.get("description", ""),
                "tools": server.get("tools", []),
                "allowed_tools": server.get("allowed_tools"),
                "status": "configured",
                "last_check": None,
            }
        _loaded = True


@tool
def list_mcps() -> str:
    """List all configured MCP server connections.

    WHEN TO USE: To discover what MCP servers are available for tool
    discovery. Shows connection status and tool counts.
    """
    _ensure_loaded()
    connections = []
    for name, info in _registry.items():
        connections.append(
            {
                "name": name,
                "description": info.get("description", ""),
                "transport": info.get("transport"),
                "status": info.get("status"),
                "tool_count": len(info.get("tools", [])),
            }
        )
    return _json({"connections": connections, "total": len(connections)})


@tool
def search_mcp_tools(connection: str = "", query: str = "", limit: int = 20) -> str:
    """Search for tools across MCP connections.

    WHEN TO USE: When you need a capability not in the standard toolset.
    Search by name or description to find relevant MCP tools.

    Args:
        connection: Filter to a specific MCP connection name
        query: Search in tool names and descriptions
        limit: Max results (default 20)
    """
    _ensure_loaded()
    results = []

    for name, info in _registry.items():
        if connection and name != connection:
            continue
        for t in info.get("tools", []):
            tool_name = t.get("name", "") if isinstance(t, dict) else str(t)
            tool_desc = t.get("description", "") if isinstance(t, dict) else ""

            if query:
                q = query.lower()
                if q not in tool_name.lower() and q not in tool_desc.lower():
                    continue

            results.append(
                {
                    "connection": name,
                    "tool": tool_name,
                    "description": tool_desc[:200],
                }
            )
            if len(results) >= limit:
                break

    return _json({"results": results, "total": len(results)})


@tool
def get_mcp_tool_schema(connection: str, tool_name: str) -> str:
    """Get the input schema for a specific MCP tool.

    WHEN TO USE: Before calling an MCP tool, get its schema to
    understand required parameters.
    """
    _ensure_loaded()
    info = _registry.get(connection)
    if not info:
        return _json({"error": f"connection not found: {connection}"})

    for t in info.get("tools", []):
        name = t.get("name", "") if isinstance(t, dict) else str(t)
        if name == tool_name:
            return _json(
                {
                    "connection": connection,
                    "tool": tool_name,
                    "schema": t.get("inputSchema", t.get("schema", {}))
                    if isinstance(t, dict)
                    else {},
                    "description": t.get("description", "") if isinstance(t, dict) else "",
                }
            )
    return _json({"error": f"tool not found: {tool_name} in {connection}"})


@tool
def call_mcp(connection: str, tool_name: str, arguments_json: str = "{}") -> str:
    """Call a tool on an MCP server.

    WHEN TO USE: After finding a relevant MCP tool via search_mcp_tools,
    call it with the required arguments.

    Args:
        connection: The MCP connection name
        tool_name: The tool to call
        arguments_json: JSON string of tool arguments
    """
    _ensure_loaded()
    info = _registry.get(connection)
    if not info:
        return _json({"error": f"connection not found: {connection}"})

    try:
        args = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError:
        return _json({"error": "invalid arguments_json"})

    # Check allowed_tools filter
    allowed = info.get("allowed_tools")
    if allowed and tool_name not in allowed:
        return _json({"error": f"tool '{tool_name}' not in allowed_tools for {connection}"})

    # Dispatch based on transport
    transport = info.get("transport", "stdio")
    url = info.get("url", "")

    if transport == "http" and url:
        import httpx

        try:
            resp = httpx.post(
                f"{url.rstrip('/')}/tools/{tool_name}",
                json={"arguments": args},
                timeout=60.0,
                headers={"Content-Type": "application/json"},
            )
            return _json(
                {
                    "result": resp.json() if resp.status_code == 200 else resp.text,
                    "status_code": resp.status_code,
                }
            )
        except Exception as e:
            return _json({"error": f"{type(e).__name__}: {e}"})

    return _json(
        {
            "error": f"unsupported transport: {transport}",
            "hint": "Only 'http' transport is currently supported for direct calls",
        }
    )


MCP_REGISTRY_TOOLS = [list_mcps, search_mcp_tools, get_mcp_tool_schema, call_mcp]
