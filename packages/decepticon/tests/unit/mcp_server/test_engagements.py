"""Tests for decepticon.mcp_server.engagements (LangGraph SDK bridge).

Uses an in-memory fake LangGraph client rather than a mock — the fake records
what the bridge sent so the scope/RoE payload and continuation contract are
asserted directly.
"""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError

from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.engagements import EngagementClient
from decepticon.mcp_server.server import build_server
from decepticon.tools.interaction.complete_planning import planning_bundle_digest


class _Part:
    def __init__(self, event: str, data: Any) -> None:
        self.event = event
        self.data = data
        self.id = None


class _FakeThreads:
    def __init__(self, threads: list[dict[str, Any]] | None) -> None:
        self._threads = threads or []
        self.state: dict[str, Any] = {"values": {}}

    async def create(self) -> dict[str, Any]:
        return {"thread_id": "t-123"}

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        return self.state

    async def search(
        self, *, limit: int, sort_by: str | None = None, sort_order: str | None = None
    ) -> list[dict[str, Any]]:
        return self._threads[:limit]


class _FakeRuns:
    def __init__(self, runs: list[dict[str, Any]] | None, stream: list[_Part] | None) -> None:
        self._runs = runs if runs is not None else []
        self._stream = stream or []
        self.create_calls: list[dict[str, Any]] = []
        self.cancelled: tuple[str, str] | None = None

    async def create(self, thread_id: str, *, assistant_id: str, **kwargs: Any) -> dict[str, Any]:
        self.create_calls.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        return {"run_id": "r-456", "status": "pending"}

    async def list(self, thread_id: str, *, limit: int = 10, **_: Any) -> list[dict[str, Any]]:
        return self._runs[:limit]

    async def cancel(self, thread_id: str, run_id: str) -> None:
        self.cancelled = (thread_id, run_id)

    async def join_stream(
        self, thread_id: str, run_id: str, *, stream_mode: Any = None
    ) -> AsyncIterator[_Part]:
        for part in self._stream:
            yield part


class _FakeAssistants:
    async def search(self) -> list[dict[str, Any]]:
        return [{"assistant_id": "a-1", "graph_id": "decepticon", "name": "decepticon"}]


class _FakeClient:
    def __init__(
        self,
        *,
        runs: list[dict[str, Any]] | None = None,
        threads: list[dict[str, Any]] | None = None,
        stream: list[_Part] | None = None,
    ) -> None:
        self.threads = _FakeThreads(threads)
        self.runs = _FakeRuns(runs, stream)
        self.assistants = _FakeAssistants()


def _config() -> ServerConfig:
    return ServerConfig(
        langgraph_url="http://test:2024",
        default_assistant="decepticon",
        request_timeout_seconds=60.0,
    )


async def test_start_dispatches_background_run_with_scope() -> None:
    fake = _FakeClient()
    client = EngagementClient(_config(), client=fake)

    result = await client.start(
        targets=["https://example.com"],
        instruction="In scope: example.com. Out of scope: prod DB.",
        scan_mode="standard",
        engagement_name="eng-1",
        assistant="decepticon",
        workspace_path="/workspace",
    )

    assert (result.thread_id, result.run_id, result.status) == ("t-123", "r-456", "pending")
    call = fake.runs.create_calls[0]
    assert call["assistant_id"] == "decepticon"
    assert call["config"]["configurable"]["engagement_name"] == "eng-1"
    assert call["config"]["configurable"]["workspace_path"] == "/workspace"
    scope = call["input"]["scan_scope"]
    assert scope["targets"] == ["https://example.com"]
    assert "Out of scope" in scope["instruction"]


async def test_start_tool_requires_selected_workspace_and_current_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "eng-1")
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", str(tmp_path))
    manager = build_server(_config(), client=fake)._tool_manager
    arguments = {
        "targets": ["https://example.com"],
        "instruction": "Only example.com is in scope",
        "engagement_name": "eng-1",
        "assistant": "decepticon",
    }
    with pytest.raises(ToolError, match="must match"):
        await manager.call_tool("decepticon_start_engagement", {**arguments, "engagement_name": "other"})
    with pytest.raises(ToolError, match="approve"):
        await manager.call_tool("decepticon_start_engagement", arguments)
    await manager.call_tool(
        "decepticon_start_engagement", {**arguments, "assistant": "soundwave"}
    )
    plan = tmp_path / "plan"
    plan.mkdir()
    for filename in (
        "roe.json", "threat-profile.json", "conops.json", "deconfliction.json",
        "contact.json", "data-handling.json", "abort.json", "cleanup.json",
    ):
        (plan / filename).write_text("{}", encoding="utf-8")
    digest = planning_bundle_digest(tmp_path)
    (tmp_path / ".planning-draft-ready").write_text(digest, encoding="utf-8")
    (tmp_path / ".red-approved").write_text(digest, encoding="utf-8")
    await manager.call_tool("decepticon_start_engagement", arguments)
    assert fake.runs.create_calls[1]["config"]["configurable"]["workspace_path"] == str(tmp_path)
    (plan / "roe.json").write_text('{"changed":true}', encoding="utf-8")
    with pytest.raises(ToolError, match="changed after approval"):
        await manager.call_tool("decepticon_start_engagement", arguments)


async def test_send_tool_rejects_other_workspace_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "eng-1")
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT_WORKSPACE", str(tmp_path))
    manager = build_server(_config(), client=fake)._tool_manager
    arguments = {"thread_id": "t-123", "message": "continue", "assistant": "soundwave"}
    fake.threads.state = {"values": {"engagement_name": "other", "workspace_path": str(tmp_path)}}
    with pytest.raises(ToolError, match="does not belong"):
        await manager.call_tool("decepticon_send_message", arguments)
    fake.threads.state = {"values": {"engagement_name": "eng-1", "workspace_path": str(tmp_path)}}
    await manager.call_tool("decepticon_send_message", arguments)
    assert len(fake.runs.create_calls) == 1
    with pytest.raises(ToolError, match="approve"):
        await manager.call_tool(
            "decepticon_send_message", {**arguments, "assistant": "decepticon"}
        )


async def test_stdio_start_rejects_unselected_and_unapproved_workspace(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "decepticon.mcp_server", "--transport", "stdio"],
        env={
            **os.environ,
            "DECEPTICON_SKIP_BOOT": "1",
            "DECEPTICON_ENGAGEMENT": "eng-1",
            "DECEPTICON_ENGAGEMENT_WORKSPACE": str(tmp_path),
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            arguments = {
                "targets": ["https://example.com"],
                "instruction": "Only example.com is in scope",
                "assistant": "decepticon",
            }
            wrong = await session.call_tool(
                "decepticon_start_engagement", {**arguments, "engagement_name": "other"}
            )
            assert wrong.isError is True
            denied = await session.call_tool(
                "decepticon_start_engagement", {**arguments, "engagement_name": "eng-1"}
            )
            assert denied.isError is True


async def test_send_message_enqueues_and_resolves_assistant_from_thread() -> None:
    fake = _FakeClient(runs=[{"run_id": "r-old", "assistant_id": "recon", "status": "success"}])
    client = EngagementClient(_config(), client=fake)

    handle = await client.send_message(thread_id="t-1", message="focus on the API")

    assert handle.assistant == "recon"
    call = fake.runs.create_calls[0]
    assert call["multitask_strategy"] == "enqueue"
    assert call["input"]["messages"][0]["content"] == "focus on the API"


async def test_send_message_explicit_assistant_skips_lookup() -> None:
    fake = _FakeClient(runs=[])
    client = EngagementClient(_config(), client=fake)
    handle = await client.send_message(thread_id="t-1", message="hi", assistant="soundwave")
    assert handle.assistant == "soundwave"


async def test_send_message_model_command_sets_override_and_body() -> None:
    fake = _FakeClient(runs=[{"run_id": "r", "assistant_id": "decepticon", "status": "success"}])
    client = EngagementClient(_config(), client=fake)
    await client.send_message(
        thread_id="t-1", message="/model anthropic/claude-opus-4-8 keep digging"
    )
    call = fake.runs.create_calls[0]
    assert call["config"]["configurable"]["model_override"] == "anthropic/claude-opus-4-8"
    assert call["input"]["messages"] == [{"role": "user", "content": "keep digging"}]


async def test_send_message_bare_model_command_rebinds_without_message() -> None:
    fake = _FakeClient(runs=[{"run_id": "r", "assistant_id": "decepticon", "status": "success"}])
    client = EngagementClient(_config(), client=fake)
    await client.send_message(thread_id="t-1", message="/model openai/gpt-5.5")
    call = fake.runs.create_calls[0]
    assert call["config"]["configurable"]["model_override"] == "openai/gpt-5.5"
    assert call["input"]["messages"] == []


async def test_send_message_plain_text_passes_no_override_config() -> None:
    fake = _FakeClient(runs=[{"run_id": "r", "assistant_id": "recon", "status": "success"}])
    client = EngagementClient(_config(), client=fake)
    await client.send_message(thread_id="t-1", message="/modeller is not a command")
    call = fake.runs.create_calls[0]
    assert "config" not in call
    assert call["input"]["messages"] == [{"role": "user", "content": "/modeller is not a command"}]


async def test_list_engagements_maps_threads() -> None:
    fake = _FakeClient(
        threads=[
            {
                "thread_id": "t-9",
                "status": "idle",
                "created_at": "2026-01-01",
                "updated_at": "2026-01-02",
                "values": {"engagement_name": "eng-x"},
            }
        ]
    )
    client = EngagementClient(_config(), client=fake)
    rows = await client.list_engagements(limit=20)
    assert rows[0].thread_id == "t-9"
    assert rows[0].engagement_name == "eng-x"


async def test_cancel_active_run() -> None:
    fake = _FakeClient(runs=[{"run_id": "r-1", "status": "running"}])
    client = EngagementClient(_config(), client=fake)
    assert await client.cancel("t-1") == "r-1"
    assert fake.runs.cancelled == ("t-1", "r-1")


async def test_cancel_when_no_active_run_returns_none() -> None:
    fake = _FakeClient(runs=[{"run_id": "r-1", "status": "success"}])
    client = EngagementClient(_config(), client=fake)
    assert await client.cancel("t-1") is None
    assert fake.runs.cancelled is None


async def test_watch_collects_stream_events() -> None:
    stream = [_Part("custom", {"type": "recon_step"}), _Part("updates", {"node": "exploit"})]
    client = EngagementClient(_config(), client=_FakeClient(stream=stream))
    events, truncated = await client.watch(
        thread_id="t", run_id="r", max_seconds=5.0, max_events=10
    )
    assert [e.event for e in events] == ["custom", "updates"]
    assert "recon_step" in events[0].data
    assert truncated is False


async def test_watch_truncates_at_event_cap() -> None:
    stream = [_Part("custom", {"i": n}) for n in range(5)]
    client = EngagementClient(_config(), client=_FakeClient(stream=stream))
    events, truncated = await client.watch(thread_id="t", run_id="r", max_seconds=5.0, max_events=2)
    assert len(events) == 2
    assert truncated is True


async def test_list_graphs_maps_assistants() -> None:
    client = EngagementClient(_config(), client=_FakeClient())
    graphs = await client.list_graphs()
    assert graphs[0].graph_id == "decepticon"
    assert graphs[0].assistant_id == "a-1"
