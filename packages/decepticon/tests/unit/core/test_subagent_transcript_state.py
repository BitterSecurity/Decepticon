"""Durable sub-agent transcript persistence.

``StreamingRunnable`` emits sub-agent activity as transient LangGraph custom
events. Those are lost on reconnect / for completed runs because nothing about
them lands in the checkpoint. This module proves the durable counterpart: the
wrapper now ALSO returns a ``subagent_transcripts`` state key (forwarded to the
parent graph by deepagents' ``task()`` tool), and the orchestrator channel's
reducer accumulates those entries across calls.

Covered here:
  * invoke()/ainvoke() return a non-empty ``subagent_transcripts`` list whose
    entries are the same event dicts that were streamed (same ``type`` values).
  * the persisted tool-result copy is capped while the streamed copy stays full.
  * ``reduce_concat_transcript`` concatenates across two sub-agent calls and is
    idempotent on ``None`` / empty updates.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable

from decepticon.core.subagent_streaming import (
    TRANSCRIPT_STATE_KEY,
    StreamingRunnable,
    clear_subagent_renderer,
    set_subagent_renderer,
)
from decepticon.middleware.state_reducers import reduce_concat_transcript
from decepticon.middleware.subagent_transcript_state import SubagentTranscriptState


class FakeSubagentRunnable(Runnable):
    """Minimal fake compiled subagent that replays a scripted message stream.

    Each yielded state is a cumulative ``{"messages": [...]}`` snapshot, exactly
    how a real LangGraph ``stream(stream_mode="values")`` surfaces growth.
    """

    def __init__(self, message_growth: list[list[Any]]):
        self._snapshots = [{"messages": list(m)} for m in message_growth]

    def stream(self, input: Any, config: Any = None, stream_mode: str = "values", **kwargs: Any):
        yield from self._snapshots

    async def astream(
        self, input: Any, config: Any = None, stream_mode: str = "values", **kwargs: Any
    ):
        for snap in self._snapshots:
            yield snap

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:  # pragma: no cover
        return self._snapshots[-1] if self._snapshots else {"messages": []}

    async def ainvoke(
        self, input: Any, config: Any = None, **kwargs: Any
    ) -> Any:  # pragma: no cover
        return self._snapshots[-1] if self._snapshots else {"messages": []}


@pytest.fixture
def writer_renderer():
    """Active renderer so the wrapper streams (not the no-channel fast path)."""

    class _R:
        def on_subagent_start(self, *a: Any, **k: Any) -> None: ...
        def on_subagent_end(self, *a: Any, **k: Any) -> None: ...
        def on_subagent_message(self, *a: Any, **k: Any) -> None: ...
        def on_subagent_tool_call(self, *a: Any, **k: Any) -> None: ...
        def on_subagent_tool_result(self, *a: Any, **k: Any) -> None: ...

    token = set_subagent_renderer(_R())
    try:
        yield
    finally:
        clear_subagent_renderer(token)


def _tool_using_growth() -> list[list[Any]]:
    """A realistic stream: AI thinks + calls a tool, then the tool result, then
    a final AI message. Each snapshot is cumulative."""
    tool_call_msg = AIMessage(
        content="scanning now",
        tool_calls=[
            {"id": "tc-1", "name": "bash", "args": {"command": "nmap -sV t"}, "type": "tool_call"}
        ],
    )
    tool_result_msg = ToolMessage(content="22/tcp open ssh", tool_call_id="tc-1")
    final_msg = AIMessage(content="found ssh")
    base = [HumanMessage(content="scan target")]
    return [
        base + [tool_call_msg],
        base + [tool_call_msg, tool_result_msg],
        base + [tool_call_msg, tool_result_msg, final_msg],
    ]


def _types(transcript: list[dict]) -> list[str]:
    return [e["type"] for e in transcript]


class TestTranscriptInReturnedState:
    def test_invoke_returns_transcript_with_expected_event_types(
        self, writer_renderer: None
    ) -> None:
        wrapper = StreamingRunnable(FakeSubagentRunnable(_tool_using_growth()), "recon")

        out = wrapper.invoke({"messages": [HumanMessage(content="scan target")]})

        assert isinstance(out, dict)
        transcript = out[TRANSCRIPT_STATE_KEY]
        assert isinstance(transcript, list) and transcript, "transcript must be a non-empty list"
        assert all(isinstance(e, dict) for e in transcript)

        types = _types(transcript)
        # Brackets the run with start/end and carries the tool round-trip.
        assert types[0] == "subagent_start"
        assert types[-1] == "subagent_end"
        for expected in ("subagent_tool_call", "subagent_tool_result", "subagent_message"):
            assert expected in types, f"missing {expected} in {types}"

        # Every persisted entry is tagged with this invocation's session_id.
        session_ids = {e["session_id"] for e in transcript}
        assert len(session_ids) == 1
        assert all(
            datetime.fromisoformat(e["created_at"]).tzinfo == timezone.utc for e in transcript
        )

    @pytest.mark.asyncio
    async def test_ainvoke_returns_transcript_with_expected_event_types(
        self, writer_renderer: None
    ) -> None:
        wrapper = StreamingRunnable(FakeSubagentRunnable(_tool_using_growth()), "recon")

        out = await wrapper.ainvoke({"messages": [HumanMessage(content="scan target")]})

        transcript = out[TRANSCRIPT_STATE_KEY]
        assert isinstance(transcript, list) and transcript
        types = _types(transcript)
        assert types[0] == "subagent_start"
        assert types[-1] == "subagent_end"
        assert "subagent_tool_result" in types
        assert all(
            datetime.fromisoformat(e["created_at"]).tzinfo == timezone.utc for e in transcript
        )

    def test_persisted_tool_result_is_capped_while_stream_stays_full(
        self, writer_renderer: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DECEPTICON_SUBAGENT_TRANSCRIPT_RESULT_CAP", "10")
        big = "A" * 5000
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "bash", "args": {}, "type": "tool_call"}],
        )
        tool_result_msg = ToolMessage(content=big, tool_call_id="tc-1")
        base = [HumanMessage(content="go")]
        growth = [base + [tool_call_msg], base + [tool_call_msg, tool_result_msg]]

        wrapper = StreamingRunnable(FakeSubagentRunnable(growth), "recon")
        out = wrapper.invoke({"messages": base})

        persisted_results = [
            e for e in out[TRANSCRIPT_STATE_KEY] if e["type"] == "subagent_tool_result"
        ]
        assert persisted_results, "expected a persisted tool_result event"
        capped = persisted_results[0]["content"]
        assert capped.startswith("A" * 10)
        assert capped.endswith("…[truncated]")
        assert len(capped) < len(big)


class TestReduceConcatTranscript:
    def test_concatenates_across_two_calls(self) -> None:
        first = [{"type": "subagent_start"}, {"type": "subagent_end"}]
        second = [{"type": "subagent_start"}, {"type": "subagent_tool_call"}]
        merged = reduce_concat_transcript(first, second)
        assert merged == first + second
        # Order-stable and append-only (no dedup, no reorder).
        assert [e["type"] for e in merged] == [
            "subagent_start",
            "subagent_end",
            "subagent_start",
            "subagent_tool_call",
        ]

    def test_idempotent_on_none_and_empty(self) -> None:
        assert reduce_concat_transcript(None, None) == []
        assert reduce_concat_transcript(None, []) == []
        existing = [{"type": "subagent_start"}]
        assert reduce_concat_transcript(existing, None) is existing
        assert reduce_concat_transcript(None, [{"type": "x"}]) == [{"type": "x"}]


class TestSchemaExtendsAgentState:
    def test_schema_declares_transcript_channel_and_keeps_base_channels(self) -> None:
        # TypedDict subclasses don't keep the base in __mro__ or support
        # issubclass(); the real proof that we EXTENDED (not replaced) AgentState
        # is that the subclass __annotations__ merge in the base channels. If
        # this regressed to a bare TypedDict, "messages"/"jump_to" would vanish
        # and create_agent would drop deepagents' own state surface.
        keys = SubagentTranscriptState.__annotations__
        assert TRANSCRIPT_STATE_KEY in keys
        # Base channels preserved (inherited from langchain AgentState).
        assert "messages" in keys
        assert "jump_to" in keys
        assert "structured_response" in keys
