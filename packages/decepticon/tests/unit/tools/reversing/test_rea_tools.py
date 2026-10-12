from __future__ import annotations

import json

import httpx
import pytest

from decepticon.tools.reversing import rea


def test_rea_tools_are_configured_only_with_a_runtime_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECEPTICON_REA_URL", raising=False)
    assert not rea.rea_configured()
    monkeypatch.setenv("DECEPTICON_REA_URL", "http://rea-mcp:8082")
    assert rea.rea_configured()


@pytest.mark.asyncio
async def test_rea_find_tools_preserves_binary_and_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def request(method: str, path: str, payload: dict | None = None) -> str:
        calls.append((method, path, payload))
        return json.dumps({"tools": [{"name": "analyze_function"}], "nextCursor": 30})

    monkeypatch.setattr(rea, "_request", request)
    result = await rea.rea_find_tools.ainvoke(
        {
            "binary_path": "/workspace/sample",
            "query": "function",
            "cursor": 20,
            "limit": 10,
        }
    )
    assert json.loads(result)["nextCursor"] == 30
    assert calls == [
        (
            "POST",
            "/find",
            {
                "binary_path": "/workspace/sample",
                "query": "function",
                "cursor": 20,
                "limit": 10,
            },
        )
    ]


@pytest.mark.asyncio
async def test_rea_find_tools_keeps_complete_schema_as_model_safe_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = {
        "name": "analyze_function",
        "description": "Analyze a function",
        "inputSchema": {
            "properties": {"procedure": {"$ref": "#/$defs/procedure"}},
            "$defs": {"procedure": {"type": "string"}},
        },
    }

    async def request(_method: str, _path: str, _payload: dict | None = None) -> str:
        return json.dumps({"total": 1, "nextCursor": None, "tools": [schema]})

    monkeypatch.setattr(rea, "_request", request)
    result = json.loads(
        await rea.rea_find_tools.ainvoke({"binary_path": "/workspace/sample", "query": "analyze"})
    )
    assert result["total"] == 1
    assert result["nextCursor"] is None
    assert result["tools"][0]["name"] == "analyze_function"
    assert "inputSchema" not in result["tools"][0]
    assert json.loads(result["tools"][0]["toolSchemaJson"]) == schema


@pytest.mark.asyncio
async def test_rea_call_tool_passes_exact_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def request(method: str, path: str, payload: dict | None = None) -> str:
        calls.append((method, path, payload))
        return json.dumps({"evidence": {"evidence_id": "ev_example"}})

    monkeypatch.setattr(rea, "_request", request)
    result = await rea.rea_call_tool.ainvoke(
        {
            "binary_path": "/workspace/sample",
            "name": "analyze_function",
            "arguments_json": '{"procedure":"main"}',
        }
    )
    assert json.loads(result)["evidence"]["evidence_id"] == "ev_example"
    assert calls == [
        (
            "POST",
            "/call",
            {
                "binary_path": "/workspace/sample",
                "name": "analyze_function",
                "arguments": {"procedure": "main"},
            },
        )
    ]


@pytest.mark.asyncio
async def test_rea_call_tool_rejects_non_object_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(_method: str, _path: str, _payload: dict | None = None) -> str:
        raise AssertionError("invalid arguments reached the REA service")

    monkeypatch.setattr(rea, "_request", request)
    result = await rea.rea_call_tool.ainvoke(
        {
            "binary_path": "/workspace/sample",
            "name": "analyze_function",
            "arguments_json": "[]",
        }
    )
    assert json.loads(result)["error"] == "arguments_json must be a JSON object"


@pytest.mark.asyncio
async def test_rea_provider_diagnostic_reaches_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_REA_URL", "http://rea-mcp:8082")

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"isError": True, "diagnostic": ["provider_timeout"]})

    class Client(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(rea.httpx, "AsyncClient", Client)
    result = json.loads(
        await rea.rea_call_tool.ainvoke(
            {
                "binary_path": "/workspace/sample",
                "name": "analyze_function",
                "arguments_json": "{}",
            }
        )
    )
    assert result == {"isError": True, "diagnostic": ["provider_timeout"], "statusCode": 422}
