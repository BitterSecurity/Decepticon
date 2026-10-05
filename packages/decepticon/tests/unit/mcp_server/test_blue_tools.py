from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from decepticon.mcp_server.blue import BlueClient
from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.server import build_server


def _server(client: httpx.AsyncClient):
    config = ServerConfig(
        langgraph_url="http://langgraph:2024",
        default_assistant="decepticon",
        request_timeout_seconds=5,
    )
    return build_server(config, client=object(), blue_client=BlueClient(client))


async def test_blue_tools_read_live_receiver_and_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLUE_SENSOR_URL", "http://receiver:8082")
    monkeypatch.setenv("BLUE_MONITOR_URL", "http://monitor:8085")
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "receiver" and request.url.path == "/metrics":
            return httpx.Response(200, json={"latest_seq": 7, "collector_available": True})
        if request.url.host == "monitor" and request.url.path == "/metrics":
            return httpx.Response(200, json={"watch_enabled": True})
        if request.url.path == "/events":
            return httpx.Response(200, json={"events": [{"seq": 7}], "next_after": 7})
        if request.url.path == "/notifications":
            return httpx.Response(200, json={"notifications": [{"seq": 3}], "next_after": 3})
        if request.url.path == "/sources":
            return httpx.Response(200, json={"sources": [{"source": "target-log-file"}]})
        if request.url.path == "/incidents":
            return httpx.Response(200, json={"incidents": [{"id": "incident-1"}]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager = _server(client)._tool_manager
        assert (await manager.call_tool("decepticon_blue_status", {}))["monitor"]["watch_enabled"]
        assert (await manager.call_tool("decepticon_blue_events", {"after": 6}))["next_after"] == 7
        assert (await manager.call_tool("decepticon_blue_notifications", {"after": 2}))[
            "next_after"
        ] == 3
        assert (await manager.call_tool("decepticon_blue_sources", {}))["sources"]
        assert (await manager.call_tool("decepticon_blue_incidents", {}))["incidents"]
        tool = manager.get_tool("decepticon_blue_events")
        assert tool is not None and tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
    assert "http://receiver:8082/events?after=6&limit=20" in seen
    assert "http://monitor:8085/notifications?after=2&limit=20" in seen


async def test_blue_body_is_bounded_and_rejects_bad_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_SENSOR_URL", "http://receiver:8082")
    ref = "a" * 32

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/bodies/{ref}"
        return httpx.Response(200, content=b"z" * 100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager = _server(client)._tool_manager
        result = await manager.call_tool("decepticon_blue_body", {"ref": ref, "max_bytes": 16})
        assert result == {
            "ref": ref,
            "total_bytes": 100,
            "preview_bytes": 16,
            "preview_utf8": "z" * 16,
            "preview_limited": True,
        }
        with pytest.raises(Exception, match="body reference"):
            await manager.call_tool("decepticon_blue_body", {"ref": "../bad"})


async def test_blue_search_timeline_validation_and_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BLUE_SENSOR_URL", "http://receiver:8082")
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/search":
            return httpx.Response(503)
        return httpx.Response(200, json={"events": [], "has_more": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager = _server(client)._tool_manager
        with pytest.raises(Exception, match="field must be"):
            await manager.call_tool("decepticon_blue_search", {"field": "path", "value": "x"})
        with pytest.raises(Exception, match="503 Service Unavailable"):
            await manager.call_tool(
                "decepticon_blue_search", {"field": "request_id", "value": "req-1"}
            )
        with pytest.raises(Exception, match="window"):
            await manager.call_tool(
                "decepticon_blue_timeline",
                {"start_at": "2026-10-05T00:00:00Z", "end_at": "2026-10-05T02:00:00Z"},
            )
        result = await manager.call_tool(
            "decepticon_blue_timeline",
            {"start_at": "2026-10-05T00:00:00Z", "end_at": "2026-10-05T00:30:00Z"},
        )
        assert result["events"] == []
    assert paths == ["/search", "/timeline"]


async def test_blue_events_over_real_stdio_mcp() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/events?after=0&limit=20":
                payload = {"events": [{"seq": 1, "source": "target-log-file"}], "next_after": 1}
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_port}"
    env = {**os.environ, "DECEPTICON_SKIP_BOOT": "1", "BLUE_SENSOR_URL": address}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "decepticon.mcp_server", "--transport", "stdio"],
        env=env,
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert "decepticon_blue_events" in {tool.name for tool in tools.tools}
                result = await session.call_tool("decepticon_blue_events", {"after": 0})
                assert result.structuredContent is not None
                assert result.structuredContent["events"][0]["seq"] == 1
                invalid = await session.call_tool("decepticon_blue_events", {"after": -1})
                assert invalid.isError is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
