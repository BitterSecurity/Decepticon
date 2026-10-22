"""MCP engagements start only through orchestration graphs.

Specialists (recon, exploit, the operators, …) are execution sub-agents the
orchestrator reaches via its task() tool. Started directly they skip the
orchestrator's RoE/OPPLAN generation, so RoEGuardrailMiddleware finds no
plan/roe.json and falls back to audit-only mode. decepticon_start_engagement
must therefore refuse a specialist assistant and create no thread/run, matching
the CLI's documented "orchestration agents only" contract.
"""

from __future__ import annotations

from mcp.shared.memory import create_connected_server_and_client_session

from decepticon.mcp_server.server import build_server
from decepticon.mcp_server.tools_lifecycle import is_engagement_entry_graph

from .test_engagements import _config, _FakeClient


def test_orchestration_graphs_are_entry_points() -> None:
    # Main orchestrator (SUBAGENT), planner (planning), blue observer
    # (observation), plugin orchestrator (SUBAGENT).
    for name in ("decepticon", "soundwave", "blue_cell", "vulnresearch"):
        assert is_engagement_entry_graph(name) is True, name


def test_specialists_are_not_entry_points() -> None:
    for name in ("recon", "exploit", "postexploit", "osint_operator", "ad_operator", "scanner"):
        assert is_engagement_entry_graph(name) is False, name


async def test_start_engagement_rejects_specialist_without_creating_thread() -> None:
    fake = _FakeClient()
    server = build_server(_config(), client=fake)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "decepticon_start_engagement",
            {"targets": ["https://example.com"], "assistant": "recon"},
        )
    assert result.isError
    assert fake.threads.created == 0
    assert fake.runs.create_calls == []


async def test_start_engagement_allows_orchestration_graph() -> None:
    fake = _FakeClient()
    server = build_server(_config(), client=fake)
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(
            "decepticon_start_engagement",
            {"targets": ["https://example.com"], "assistant": "soundwave"},
        )
    assert not result.isError
    assert fake.threads.created == 1
