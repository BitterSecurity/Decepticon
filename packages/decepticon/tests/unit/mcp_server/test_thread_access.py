"""Selected-workspace isolation through the actual MCP protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.server import build_server


class _Threads:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        selected = thread_id in {"selected-thread", "misplaced-thread"}
        return {
            "values": {
                "engagement_name": "selected" if selected else "foreign",
                "workspace_path": str(self.workspace)
                if thread_id == "selected-thread"
                else "/foreign",
                "messages": [],
            }
        }

    async def search(self, **_: Any) -> list[dict[str, Any]]:
        return [
            {"thread_id": "foreign-thread", "values": {"engagement_name": "foreign"}},
            {"thread_id": "selected-thread", "values": {"engagement_name": "selected"}},
        ]


class _Runs:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def list(self, thread_id: str, **_: Any) -> list[dict[str, Any]]:
        return [{"run_id": "run", "status": "running", "assistant_id": "soundwave"}]

    async def cancel(self, thread_id: str, run_id: str) -> None:
        self.cancelled.append(thread_id)


class _Client:
    def __init__(self, workspace: Path) -> None:
        self.threads = _Threads(workspace)
        self.runs = _Runs()


def _server(client: _Client) -> FastMCP:
    return build_server(ServerConfig("http://test:2024", "soundwave", 10), client=client)


@pytest.mark.parametrize(
    "tool",
    [
        "decepticon_transcript",
        "decepticon_engagement_state",
        "decepticon_engagement_status",
        "decepticon_cancel_engagement",
        "decepticon_watch",
    ],
)
@pytest.mark.parametrize("thread_id", ["foreign-thread", "misplaced-thread"])
async def test_selected_workspace_rejects_foreign_thread(
    tool: str, thread_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "selected")
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", str(tmp_path))
    client = _Client(tmp_path)
    async with create_connected_server_and_client_session(_server(client)) as session:
        result = await session.call_tool(tool, {"thread_id": thread_id})
        assert result.isError
        assert "does not belong" in str(result.content)
        assert client.runs.cancelled == []
        if tool == "decepticon_cancel_engagement":
            result = await session.call_tool(tool, {"thread_id": "selected-thread"})
            assert not result.isError
            assert client.runs.cancelled == ["selected-thread"]


async def test_selected_workspace_list_excludes_foreign_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "selected")
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", str(tmp_path))
    async with create_connected_server_and_client_session(_server(_Client(tmp_path))) as session:
        result = await session.call_tool("decepticon_list_engagements", {"limit": 1})
        assert not result.isError
        assert "foreign-thread" not in str(result.content)
        assert "selected-thread" in str(result.content)
