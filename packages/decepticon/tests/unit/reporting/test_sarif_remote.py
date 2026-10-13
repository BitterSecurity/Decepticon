"""ToolNode SARIF transfers through HTTP routes and real daemon file IO."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from decepticon.backends.factory import _shared_sandbox, build_sandbox_backend
from decepticon.middleware.kg_internal.tools import build_kg_tools
from decepticon.sandbox_kernel.daemon import DaemonSandbox
from decepticon.tools.reporting.tools import report_sarif
from decepticon_core.types.kg import KnowledgeGraph, Node, NodeKind


class ToolState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    engagement_name: str
    kg_engagement: str
    workspace_path: str


class RecordingStore:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record_observations(self, observations: list, **scope: Any) -> dict:
        self.calls.append({"observations": observations, **scope})
        return {"created": len(observations)}


def dispatch(tool: Any, args: dict, state: dict, config: dict) -> dict:
    graph = StateGraph(ToolState)
    graph.add_node("tools", ToolNode([tool], handle_tool_errors=False))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    output = asyncio.run(
        graph.compile().ainvoke(
            {
                **state,
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": tool.name,
                                "args": args,
                                "id": "remote-call",
                                "type": "tool_call",
                            }
                        ],
                    )
                ],
            },
            config=config,
        )
    )
    message = output["messages"][-1]
    assert message.tool_call_id == "remote-call"
    return json.loads(message.content)


@pytest.fixture
def remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Replace network and container mount namespace only, not transfer methods."""
    server = importlib.import_module("decepticon.sandbox_server.app")
    daemon = importlib.import_module("decepticon.sandbox_kernel.daemon")
    report_module = importlib.import_module("decepticon.tools.reporting.tools")
    if hasattr(report_module, "Path"):

        def refuse_host_io(*args: Any, **kwargs: Any) -> None:
            pytest.fail("SARIF attempted agent-local IO")

        monkeypatch.setattr(report_module, "Path", refuse_host_io)
    _shared_sandbox.cache_clear()
    requests: list[tuple[str, str]] = []
    roots = {host: tmp_path / host for host in ("sandbox-a", "sandbox-b")}
    active = {"host": "sandbox-a"}

    def mounted_path(path: str) -> Path:
        assert path.startswith("/workspace/")
        return roots[active["host"]] / path.removeprefix("/")

    monkeypatch.setattr(daemon, "Path", mounted_path)
    monkeypatch.setattr(server, "_backend", DaemonSandbox())
    monkeypatch.setattr(server, "_required_token", None)
    clients = []
    for host in roots:
        client = TestClient(server.app, base_url=f"http://{host}")

        def select_mount(request: Any, host: str = host) -> None:
            active["host"] = host
            requests.append((host, request.url.path))

        client.event_hooks["request"].append(select_mount)
        backend = build_sandbox_backend({"configurable": {"sandbox_url": f"http://{host}"}})
        backend._client = client
        clients.append(client)
    partitions = []
    graph = KnowledgeGraph()
    graph.upsert_node(Node.make(NodeKind.VULNERABILITY, "Owned fixture", file="fixture.py", line=1))

    def load_graph(partition: str) -> KnowledgeGraph:
        partitions.append(partition)
        return graph

    monkeypatch.setattr("decepticon.tools.reporting.kg_adapter.load_engagement_graph", load_graph)
    yield {"roots": roots, "requests": requests, "partitions": partitions, "graph": graph}
    for client in clients:
        client.close()
    _shared_sandbox.cache_clear()


def scope(
    workspace: str, host: str = "sandbox-a", partition: str = "analyst-graph"
) -> tuple[dict, dict]:
    state = {
        "engagement_name": "owned-run",
        "workspace_path": workspace,
        "kg_engagement": partition,
    }
    return state, {"configurable": {**state, "sandbox_url": f"http://{host}"}}


@pytest.mark.parametrize("workspace", ["/workspace", "/workspace/owned-run"])
def test_remote_export_replace_reimport(workspace: str, remote: dict) -> None:
    state, config = scope(workspace)
    args = {"engagement_id": "display-name", "output_path": "/workspace/nested/report.sarif"}
    first = dispatch(report_sarif, args, state, config)
    target = remote["roots"]["sandbox-a"] / workspace.lstrip("/") / "nested/report.sarif"
    assert first["bytes"] == target.stat().st_size
    assert first["results"] == 1
    remote["graph"].upsert_node(
        Node.make(NodeKind.VULNERABILITY, "Second fixture", file="other.py", line=2)
    )
    assert dispatch(report_sarif, args, state, config)["results"] == 2
    store = RecordingStore()
    ingest_tool = build_kg_tools(store, enabled={"kg_ingest"})[0]
    result = dispatch(
        ingest_tool, {"scanner_kind": "sarif", "path": args["output_path"]}, state, config
    )
    assert result["results_processed"] == 2
    assert store.calls[0]["engagement"] == "analyst-graph"
    assert store.calls[0]["source_episode_id"] == "remote-call"
    assert remote["partitions"] == ["analyst-graph", "analyst-graph"]
    assert remote["requests"] == [
        ("sandbox-a", "/upload_files"),
        ("sandbox-a", "/upload_files"),
        ("sandbox-a", "/download_files"),
    ]


def test_per_run_endpoint_and_partition_override(remote: dict) -> None:
    for host, partition in [("sandbox-a", "first-graph"), ("sandbox-b", "second-graph")]:
        state, config = scope("/workspace/owned-run", host, partition)
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
            state,
            config,
        )
        assert (remote["roots"][host] / "workspace/owned-run/out.sarif").exists()
    assert remote["partitions"] == ["first-graph", "second-graph"]
    assert [host for host, _ in remote["requests"]] == ["sandbox-a", "sandbox-b"]


@pytest.mark.parametrize(
    "bad_scope",
    [
        {},
        {"workspace_path": "/workspace/foreign"},
        {"engagement_name": "../bad"},
        {"kg_engagement": " invalid "},
    ],
)
def test_invalid_scope_refused_before_remote_io(bad_scope: dict, remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    state = {**state, **bad_scope} if bad_scope else {}
    with pytest.raises(ValueError):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
            state,
            config,
        )
    assert remote["requests"] == []
    assert remote["partitions"] == []


def test_conflicting_config_refused_before_remote_io(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    config["configurable"]["kg_engagement"] = "foreign-graph"
    with pytest.raises(ValueError):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
            state,
            config,
        )
    assert remote["requests"] == []


def test_protected_roe_is_unchanged(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    target = remote["roots"]["sandbox-a"] / "workspace/owned-run/plan/roe.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"operator-confirmed")
    with pytest.raises(ValueError, match="read-only"):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/plan/roe.json"},
            state,
            config,
        )
    assert target.read_bytes() == b"operator-confirmed"
    assert remote["requests"] == []


def test_remote_read_error_reaches_ingest_caller(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    store = RecordingStore()
    result = dispatch(
        build_kg_tools(store, enabled={"kg_ingest"})[0],
        {"scanner_kind": "sarif", "path": "/workspace/missing.sarif"},
        state,
        config,
    )
    assert "file_not_found" in result["error"]
    assert remote["requests"] == [("sandbox-a", "/download_files")]
    assert store.calls == []


def test_remote_write_error_reaches_report_caller(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    (remote["roots"]["sandbox-a"] / "workspace/owned-run/directory").mkdir(parents=True)
    with pytest.raises(ValueError, match="is_directory"):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/directory"},
            state,
            config,
        )


def test_tool_schemas_hide_scope_and_config() -> None:
    assert set(report_sarif.tool_call_schema.model_json_schema()["properties"]) == {
        "engagement_id",
        "output_path",
    }
    ingest_tool = build_kg_tools(RecordingStore(), enabled={"kg_ingest"})[0]
    assert set(ingest_tool.tool_call_schema.model_json_schema()["properties"]) == {
        "scanner_kind",
        "path",
    }


@pytest.mark.parametrize("size, accepted", [(65536, True), (65537, False)])
def test_import_budget_checked_after_download(size: int, accepted: bool, remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    target = remote["roots"]["sandbox-a"] / "workspace/owned-run/input.sarif"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"runs": []}' + b" " * (size - 12))
    store = RecordingStore()
    result = dispatch(
        build_kg_tools(store, enabled={"kg_ingest"})[0],
        {"scanner_kind": "sarif", "path": "/workspace/input.sarif"},
        state,
        config,
    )
    assert ("error" not in result) is accepted
    if not accepted:
        assert "64 KiB" in result["error"]
    assert remote["requests"] == [("sandbox-a", "/download_files")]


def test_large_report_refused_before_upload(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    remote["graph"].upsert_node(
        Node.make(NodeKind.VULNERABILITY, "Large fixture", description="x" * 1048576)
    )
    with pytest.raises(ValueError, match="1 MiB"):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
            state,
            config,
        )
    assert remote["requests"] == []


def test_http_auth_failure_propagates(remote: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from decepticon.backends.http_sandbox import SandboxError

    server = importlib.import_module("decepticon.sandbox_server.app")
    monkeypatch.setattr(server, "_required_token", "fixture-only-token")
    state, config = scope("/workspace/owned-run")
    with pytest.raises(SandboxError, match="401"):
        dispatch(
            report_sarif,
            {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
            state,
            config,
        )


def test_ingest_invalid_scope_refused_without_download(remote: dict) -> None:
    state, config = scope("/workspace/owned-run")
    state["workspace_path"] = "/workspace/foreign"
    store = RecordingStore()
    result = dispatch(
        build_kg_tools(store, enabled={"kg_ingest"})[0],
        {"scanner_kind": "sarif", "path": "/workspace/input.sarif"},
        state,
        config,
    )
    assert "workspace mismatch" in result["error"]
    assert remote["requests"] == []
    assert store.calls == []


@pytest.mark.parametrize(
    "response",
    [
        {"files": []},
        {"files": [{"path": "/workspace/unexpected.sarif", "error": None}]},
    ],
)
def test_malformed_upload_reply_fails(response: dict, remote: dict) -> None:
    import httpx

    state, config = scope("/workspace/owned-run")
    backend = build_sandbox_backend(config)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
        base_url="http://sandbox-a",
    ) as client:
        backend._client = client
        with pytest.raises(ValueError, match="response mismatch"):
            dispatch(
                report_sarif,
                {"engagement_id": "display", "output_path": "/workspace/out.sarif"},
                state,
                config,
            )


@pytest.mark.parametrize(
    "response, error",
    [
        ({"files": []}, "response mismatch"),
        ({"files": [{"path": "/workspace/other", "data_b64": "e30="}]}, "response mismatch"),
        ({"files": [{"path": "/workspace/owned-run/in.sarif", "data_b64": None}]}, "no content"),
    ],
)
def test_malformed_download_reply_fails(response: dict, error: str, remote: dict) -> None:
    import httpx

    state, config = scope("/workspace/owned-run")
    store = RecordingStore()
    backend = build_sandbox_backend(config)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
        base_url="http://sandbox-a",
    ) as client:
        backend._client = client
        result = dispatch(
            build_kg_tools(store, enabled={"kg_ingest"})[0],
            {"scanner_kind": "sarif", "path": "/workspace/in.sarif"},
            state,
            config,
        )
    assert error in result["error"]
    assert store.calls == []
