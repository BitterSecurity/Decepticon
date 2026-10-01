"""Responses-API SSE → GenericStreamingChunk translation for the Codex handler.

The handler already asked the Codex backend to stream, but ``_completed_payload``
read ``resp.text`` — the whole body — before walking the events, so every delta
had landed before the first chunk was yielded. These tests pin the incremental
walk: text is forwarded as it arrives, function-call arguments are still
reassembled from their fragments, and a ``response.failed`` event still raises.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

_MODULE_PATH = Path(__file__).resolve().parents[5] / "config" / "codex_chatgpt_handler.py"
_spec = importlib.util.spec_from_file_location("_codex_chatgpt_handler_stream_src", _MODULE_PATH)
assert _spec is not None
assert _spec.loader is not None


class _FakeAPIError(Exception):
    def __init__(self, *_a: Any, **kw: Any) -> None:
        super().__init__(kw.get("message", ""))
        self.message = kw.get("message", "")


_FAKE_LITELLM = types.ModuleType("litellm")
_FAKE_LITELLM.CustomLLM = object
_FAKE_LITELLM.ModelResponse = object
_FAKE_LITELLM.APIError = _FakeAPIError
_FAKE_LITELLM.AuthenticationError = _FakeAPIError
_FAKE_LITELLM.BadRequestError = _FakeAPIError

_FAKE_OAUTH = types.ModuleType("oauth_token_store")
# `sys.modules.setdefault` means whichever handler test imports first installs
# this stub for the others, so it carries the union of every handler's imports.
_FAKE_OAUTH.DEFAULT_REFRESH_BUFFER_SECONDS = 300
_FAKE_OAUTH.is_timestamp_expired = lambda *_a, **_kw: False
_FAKE_OAUTH.DEFAULT_JWT_SKEW_SECONDS = 60
_FAKE_OAUTH.FileBackedCache = lambda *_a, **_kw: None
_FAKE_OAUTH.decode_jwt_payload = lambda *_a, **_kw: {}
_FAKE_OAUTH.is_jwt_expired = lambda *_a, **_kw: False
_FAKE_OAUTH.oauth_refresh_request = lambda *_a, **_kw: None
_FAKE_OAUTH.read_json_file = lambda *_a, **_kw: {}
_FAKE_OAUTH.with_retry_on_401 = lambda *_a, **_kw: None
_FAKE_OAUTH.write_json_atomic = lambda *_a, **_kw: None

_FAKE_HTTP_CLIENT = types.ModuleType("http_client")
_FAKE_HTTP_CLIENT.post = lambda *_a, **_kw: None
_FAKE_HTTP_CLIENT.async_post = lambda *_a, **_kw: None
_FAKE_HTTP_CLIENT.sync_client = lambda *_a, **_kw: None
_FAKE_HTTP_CLIENT.async_client = lambda *_a, **_kw: None


def _install(name: str, stub: types.ModuleType) -> None:
    """Install ``stub``, or top up whatever another handler test installed first.

    These stubs are process-global: the first handler test to import wins, and
    the others inherit its module. Backfilling missing attributes instead of
    plain ``setdefault`` keeps collection order from deciding whether a handler
    with different imports can load at all.
    """
    existing = sys.modules.setdefault(name, stub)
    for attr, value in vars(stub).items():
        if not attr.startswith("__") and not hasattr(existing, attr):
            setattr(existing, attr, value)


_install("litellm", _FAKE_LITELLM)
_install("oauth_token_store", _FAKE_OAUTH)
_install("httpx", types.ModuleType("httpx"))
_install("http_client", _FAKE_HTTP_CLIENT)

_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

_Accumulator = _module._CodexSseAccumulator


def _drive(*events: dict[str, Any]) -> list[dict[str, Any]]:
    """Feed events through the accumulator the way httpx delivers SSE lines."""
    accumulator = _Accumulator("auth/gpt-5.4-mini")
    chunks: list[dict[str, Any]] = []
    for event in events:
        chunks.extend(accumulator.push(f"data: {json.dumps(event)}"))
        chunks.extend(accumulator.push(""))
    chunks.extend(accumulator.close())
    return chunks


def test_text_arrives_as_incremental_deltas() -> None:
    chunks = _drive(
        {"type": "response.output_text.delta", "delta": "one"},
        {"type": "response.output_text.delta", "delta": "\n"},
        {"type": "response.output_text.delta", "delta": "two"},
        {
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 35, "output_tokens": 108}},
        },
    )

    assert [c["text"] for c in chunks] == ["one", "\n", "two", ""]
    assert [c["is_finished"] for c in chunks] == [False, False, False, True]
    final = chunks[-1]
    assert final["finish_reason"] == "stop"
    assert final["usage"] == {
        "prompt_tokens": 35,
        "completion_tokens": 108,
        "total_tokens": 143,
    }


def test_function_call_arguments_are_reassembled_before_release() -> None:
    chunks = _drive(
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "item_1",
                "call_id": "call_abc",
                "name": "get_weather",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": '{"city"',
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": ': "Seoul"}',
        },
        {
            "type": "response.output_item.done",
            "item": {"type": "function_call", "id": "item_1", "call_id": "call_abc"},
        },
        {"type": "response.completed", "response": {"usage": {}}},
    )

    tools = [c["tool_use"] for c in chunks if c["tool_use"]]
    assert len(tools) == 1
    assert tools[0]["id"] == "call_abc"
    assert tools[0]["function"]["name"] == "get_weather"
    assert json.loads(tools[0]["function"]["arguments"]) == {"city": "Seoul"}
    assert chunks[-1]["finish_reason"] == "tool_calls"


def test_arguments_survive_added_event_after_first_delta() -> None:
    events = [
        {"type": "response.function_call_arguments.delta", "item_id": "item_1", "delta": '{"city"'},
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "item_1",
                "call_id": "call_abc",
                "name": "get_weather",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_1",
            "delta": ':"Seoul"}',
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "item_1",
                "call_id": "call_abc",
                "name": "get_weather",
            },
        },
        {"type": "response.completed", "response": {"output": []}},
    ]

    chunks = _drive(*events)
    tool = next(chunk["tool_use"] for chunk in chunks if chunk["tool_use"])
    assert tool["id"] == "call_abc"
    assert json.loads(tool["function"]["arguments"]) == {"city": "Seoul"}

    response = types.SimpleNamespace(
        status_code=200,
        text="\n".join(f"data: {json.dumps(event)}" for event in events),
    )
    payload = _module._completed_payload(response)
    assert json.loads(payload["output"][0]["arguments"]) == {"city": "Seoul"}


def test_name_is_backfilled_when_only_the_done_event_carries_it() -> None:
    # Some upstream variants skip `output_item.added`; `done` is authoritative.
    chunks = _drive(
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item_9",
            "delta": "{}",
        },
        {
            "type": "response.output_item.done",
            "item": {
                "type": "function_call",
                "id": "item_9",
                "call_id": "call_z",
                "name": "task",
            },
        },
        {"type": "response.completed", "response": {}},
    )

    tools = [c["tool_use"] for c in chunks if c["tool_use"]]
    assert tools[0]["function"]["name"] == "task"
    assert tools[0]["id"] == "call_z"


def test_failed_response_raises_on_close() -> None:
    accumulator = _Accumulator("auth/gpt-5.4-mini")
    accumulator.push('data: {"type": "response.failed", "error": {"message": "upstream exploded"}}')
    # Assert against whichever litellm stub ended up installed (see _install) —
    # binding to the local class would depend on which handler test ran first.
    with pytest.raises(_module.litellm.APIError):
        accumulator.close()


def test_truncated_stream_still_terminates() -> None:
    chunks = _drive({"type": "response.output_text.delta", "delta": "partial"})

    assert [c["is_finished"] for c in chunks] == [False, True]


def test_malformed_and_done_lines_are_skipped() -> None:
    accumulator = _Accumulator("auth/gpt-5.4-mini")
    # Both push and close mutate the accumulator, so they run outside the
    # asserts — `python -O` strips assert statements along with their calls.
    malformed = accumulator.push("data: not-json")
    done_marker = accumulator.push("data: [DONE]")
    event_line = accumulator.push("event: response.completed")
    final = accumulator.close()

    assert malformed == []
    assert done_marker == []
    assert event_line == []
    assert final[0]["is_finished"] is True
