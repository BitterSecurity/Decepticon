"""Exercise per-run credentials through real OpenAI HTTP and SSE transports."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from typing_extensions import override

from decepticon.llm.factory import LLMFactory

HISTORICAL = "28b97543aba2e80761564fdf85e8c4ba4d9f5738"
THINKING = [{"type": "thinking", "thinking": "fixture only", "signature": "fixture-signature"}]


def _proxy_module() -> ModuleType:
    if os.environ.get("DECEPTICON_VERIFY_PROXY_REKEY_HISTORICAL") != "1":
        return importlib.import_module("decepticon.middleware.proxy_key_override")
    root = Path(__file__).resolve().parents[5]
    source = subprocess.check_output(
        [
            "git",
            "show",
            f"{HISTORICAL}:packages/decepticon/decepticon/middleware/proxy_key_override.py",
        ],
        cwd=root,
        text=True,
    )
    module = ModuleType("decepticon.middleware._historical_proxy_key_override")
    sys.modules[module.__name__] = module
    exec(compile(source, "<immutable historical proxy middleware>", "exec"), module.__dict__)
    return module


proxy_module = _proxy_module()


class ProtocolChatOpenAI(ChatOpenAI):
    @override
    def _get_request_payload(self, input_: Any, *, stop=None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload["metadata"] = {"protocol_fixture": "retained"}
        return payload


class TokenCounter(BaseCallbackHandler):
    def __init__(self) -> None:
        self.tokens: list[str | list[str | dict[str, Any]]] = []

    def on_llm_new_token(self, token: str | list[str | dict[str, Any]], **kwargs: Any) -> None:
        self.tokens.append(token)


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "headers": dict(self.headers), "body": body})
            usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
            common = {"id": "fixture", "created": 1, "model": body["model"]}
            if body.get("stream"):
                chunks = [
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "fixture accepted"},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                    {**common, "object": "chat.completion.chunk", "choices": [], "usage": usage},
                ]
                response = (
                    "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    + "data: [DONE]\n\n"
                ).encode()
                content_type = "text/event-stream"
            else:
                response = json.dumps(
                    {
                        **common,
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "fixture accepted",
                                    "thinking_blocks": THINKING,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": usage,
                    }
                ).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}/v1"
        monkeypatch.setenv("DECEPTICON_LLM__PROXY_URL", base)
        monkeypatch.setenv("DECEPTICON_LLM__PROXY_API_KEY", "fixture-env-master")
        monkeypatch.setenv("DECEPTICON_LLM__TIMEOUT", "120")
        monkeypatch.setenv("DECEPTICON_LLM__MAX_RETRIES", "2")
        try:
            yield base, calls
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("streaming", [True, False])
def test_rekey_preserves_wire_settings_and_authenticates_only_with_current_key(
    provider: tuple[str, list[dict[str, Any]]], streaming: bool
) -> None:
    base, calls = provider
    counter = TokenCounter()
    original = ProtocolChatOpenAI(
        model="openai/fixture",
        base_url=base,
        api_key="fixture-original-key",
        streaming=streaming,
        stream_usage=True,
        timeout=17.25,
        max_retries=0,
        max_tokens=257,
        temperature=0.3,
        top_p=0.75,
        callbacks=[counter],
        stream_chunk_timeout=23.5,
        default_headers={
            "Authorization": "Bearer fixture-original-header",
            "X-LiteLLM-API-Key": "fixture-original-key",
            "x-api-key": "fixture-customer-provider",
            "x-trace-fixture": "retained",
        },
    )
    original_headers = dict(original.default_headers or {})
    bound = proxy_module._rekey_model(original, "fixture-current-key")
    result = bound.invoke([HumanMessage(content="Synthetic transport fixture only.")])
    assert len(calls) == 1
    call = calls[0]
    assert call["path"] == "/v1/chat/completions"
    headers = {k.lower(): v for k, v in call["headers"].items()}
    assert headers["authorization"] == "Bearer fixture-current-key"
    assert headers["x-litellm-api-key"] == "fixture-current-key"
    assert headers["x-api-key"] == "fixture-customer-provider"
    assert headers["x-trace-fixture"] == "retained"
    body = call["body"]
    assert body["stream"] is streaming
    if streaming:
        assert body["stream_options"]["include_usage"] is True
        assert "fixture accepted" in counter.tokens
    assert body.get("max_completion_tokens", body.get("max_tokens")) == 257
    assert body["temperature"] == 0.3
    assert body["top_p"] == 0.75
    assert body["metadata"] == {"protocol_fixture": "retained"}
    assert result.content == "fixture accepted"
    assert result.usage_metadata is not None
    assert result.usage_metadata["total_tokens"] == 5
    assert bound.request_timeout == 17.25
    assert bound.max_retries == 0
    assert bound.stream_chunk_timeout == 23.5
    assert bound.root_client is not original.root_client
    assert bound.root_async_client is not original.root_async_client
    assert isinstance(original.openai_api_key, SecretStr)
    assert original.openai_api_key.get_secret_value() == "fixture-original-key"
    assert original.default_headers == original_headers


def test_rekey_retains_vendor_thinking_across_two_real_tool_bound_requests(
    provider: tuple[str, list[dict[str, Any]]],
) -> None:
    _, calls = provider
    original = LLMFactory()._create_chat_model("anthropic/claude-opus-5-5", 0.4, 64)
    bound = proxy_module._rekey_model(original, "fixture-current-key")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "emit_marker",
                "description": "Synthetic fixture; no implementation executes.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    first = bound.invoke([HumanMessage(content="Synthetic first turn.")], tools=tools)
    assert first.additional_kwargs.get("thinking_blocks") == THINKING
    second = bound.invoke(
        [
            HumanMessage(content="Synthetic first turn."),
            first,
            HumanMessage(content="Synthetic next turn."),
        ],
        tools=tools,
    )
    assert second.content == "fixture accepted"
    assert len(calls) == 2
    assert all(call["body"]["stream"] is False for call in calls)
    assert all("temperature" not in call["body"] for call in calls)
    assert calls[1]["body"]["messages"][1]["thinking_blocks"] == THINKING


@pytest.mark.asyncio
async def test_rekey_async_stream_uses_fresh_credentials_and_original_http_transports(
    provider: tuple[str, list[dict[str, Any]]],
) -> None:
    base, calls = provider
    with httpx.Client(trust_env=False) as sync_transport:
        async with httpx.AsyncClient(trust_env=False) as async_transport:
            original = ProtocolChatOpenAI(
                model="openai/fixture",
                base_url="http://127.0.0.1:1/unused",
                api_key="fixture-original-key",
                http_client=sync_transport,
                http_async_client=async_transport,
                streaming=True,
                stream_usage=True,
                max_retries=0,
                max_tokens=257,
            )
            bound = proxy_module._rekey_model(original, "fixture-current-key")
            result = await bound.ainvoke([HumanMessage(content="Synthetic async fixture.")])
            assert result.content == "fixture accepted"
            assert result.usage_metadata is not None
            assert result.usage_metadata["total_tokens"] == 5
            assert bound.openai_api_base == base
            assert bound.http_client is sync_transport
            assert bound.http_async_client is async_transport
            assert bound.root_async_client is not original.root_async_client
            assert len(calls) == 1
            assert calls[0]["headers"]["Authorization"] == "Bearer fixture-current-key"
            assert calls[0]["body"]["stream"] is True
            assert calls[0]["body"]["metadata"] == {"protocol_fixture": "retained"}
            assert (
                calls[0]["body"].get("max_completion_tokens", calls[0]["body"].get("max_tokens"))
                == 257
            )


def _broken_proxy_configuration() -> None:
    raise ValueError("fixture-sensitive-binding-detail")


def test_binding_failure_never_dispatches_with_the_environment_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(proxy_module.log, "handlers", [caplog.handler])
    model = ChatOpenAI(model="openai/fixture", api_key="fixture-env-master")
    monkeypatch.setattr(
        proxy_module.LLMFactory, "_resolve_proxy_config", staticmethod(_broken_proxy_configuration)
    )
    request = SimpleNamespace(
        runtime=None, state={"proxy_api_key": "fixture-current-key"}, model=model
    )
    dispatched: list[Any] = []
    with pytest.raises(RuntimeError, match="per-run proxy credential"):
        proxy_module.ProxyKeyOverrideMiddleware().wrap_model_call(
            request, lambda req: dispatched.append(req)
        )
    assert dispatched == []
    assert "per-run proxy credential binding failed" in caplog.text
    assert "fixture-sensitive-binding-detail" not in caplog.text


@pytest.mark.asyncio
async def test_async_binding_failure_never_dispatches_with_the_environment_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(proxy_module.log, "handlers", [caplog.handler])
    model = ChatOpenAI(model="openai/fixture", api_key="fixture-env-master")
    monkeypatch.setattr(
        proxy_module.LLMFactory, "_resolve_proxy_config", staticmethod(_broken_proxy_configuration)
    )
    request = SimpleNamespace(
        runtime=None, state={"proxy_api_key": "fixture-current-key"}, model=model
    )
    dispatched: list[Any] = []

    async def handler(req: Any) -> None:
        dispatched.append(req)

    with pytest.raises(RuntimeError, match="per-run proxy credential"):
        await proxy_module.ProxyKeyOverrideMiddleware().awrap_model_call(request, handler)
    assert dispatched == []
    assert "per-run proxy credential binding failed" in caplog.text
    assert "fixture-sensitive-binding-detail" not in caplog.text
