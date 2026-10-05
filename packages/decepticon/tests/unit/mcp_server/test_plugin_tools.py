from __future__ import annotations

import httpx
import pytest

from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.plugins import PluginClient
from decepticon.mcp_server.server import build_server


def _manager(client: httpx.AsyncClient):
    config = ServerConfig(
        langgraph_url="http://langgraph:2024",
        default_assistant="decepticon",
        request_timeout_seconds=5,
    )
    return build_server(
        config, client=object(), plugin_client=PluginClient(config.langgraph_url, client=client)
    )._tool_manager


async def test_plugin_bundle_tools_use_existing_api_and_set_annotations() -> None:
    requests: list[tuple[str, str]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"bundles": [{"name": "plugins", "enabled": False}]})
        return httpx.Response(
            200, json={"bundle": "plugins", "enabled": request.url.path.endswith("/enable")}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager = _manager(client)
        assert (await manager.call_tool("decepticon_plugin_bundles", {}))["bundles"][0][
            "name"
        ] == "plugins"
        assert (await manager.call_tool("decepticon_plugin_enable", {"name": "plugins"}))["enabled"]
        assert not (await manager.call_tool("decepticon_plugin_disable", {"name": "plugins"}))[
            "enabled"
        ]
        listed = manager.get_tool("decepticon_plugin_bundles")
        enabled = manager.get_tool("decepticon_plugin_enable")
        disabled = manager.get_tool("decepticon_plugin_disable")
        assert listed is not None and listed.annotations is not None
        assert enabled is not None and enabled.annotations is not None
        assert disabled is not None and disabled.annotations is not None
        assert listed.annotations.readOnlyHint is True
        assert enabled.annotations.destructiveHint is False
        assert disabled.annotations.destructiveHint is True

    assert requests == [
        ("GET", "/_decepticon/bundles"),
        ("POST", "/_decepticon/bundles/plugins/enable"),
        ("POST", "/_decepticon/bundles/plugins/disable"),
    ]


async def test_plugin_bundle_validation_and_api_errors() -> None:
    requests: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/unknown/enable"):
            return httpx.Response(404, json={"detail": "unknown bundle"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        manager = _manager(client)
        with pytest.raises(Exception, match="bundle name"):
            await manager.call_tool("decepticon_plugin_enable", {"name": "../plugins"})
        with pytest.raises(Exception, match="cannot be disabled"):
            await manager.call_tool("decepticon_plugin_disable", {"name": "standard"})
        with pytest.raises(Exception, match="404 Not Found"):
            await manager.call_tool("decepticon_plugin_enable", {"name": "unknown"})
        with pytest.raises(Exception, match="non-object"):
            await manager.call_tool("decepticon_plugin_bundles", {})
    assert requests == ["/_decepticon/bundles/unknown/enable", "/_decepticon/bundles"]
