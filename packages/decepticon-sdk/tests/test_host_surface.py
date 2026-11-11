"""Behavior of ``decepticon_sdk.host`` against the real framework.

The stability manifest only proves the names import. These tests prove the
contract they stand for: the exports are the framework's own objects, former
private import paths keep working, per-run routing and the OpenShell trust
boundary behave as documented, and every key in ``HostRunConfigurable`` is
actually consumed by the framework. Everything runs real framework code; no
module is replaced with a fake.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, TypedDict, cast

import pytest

host = pytest.importorskip("decepticon_sdk.host")


@pytest.fixture
def http_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as a host whose provider is the HTTP transport, with no ambient endpoint."""
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "http")
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)


@pytest.fixture
def no_resolver(monkeypatch: pytest.MonkeyPatch) -> Any:
    """No trusted OpenShell resolver installed, and the bundled one disabled."""
    monkeypatch.setenv("DECEPTICON_OPENSHELL_LOCAL", "0")
    host.configure_openshell_resolver(None)
    yield
    host.configure_openshell_resolver(None)


# ── Re-export identity and back-compat ──────────────────────────────────


REEXPORTS = {
    "HostRunConfigurable": ("decepticon_core.types.host", "HostRunConfigurable"),
    "HOST_RUN_CONFIGURABLE_KEYS": ("decepticon_core.types.host", "HOST_RUN_CONFIGURABLE_KEYS"),
    "build_sandbox_backend": ("decepticon.backends.factory", "build_sandbox_backend"),
    "configure_openshell_resolver": ("decepticon.backends.factory", "configure_openshell_resolver"),
    "is_openshell_sandbox": ("decepticon.backends.factory", "is_openshell_sandbox"),
    "make_agent_backend": ("decepticon.backends", "make_agent_backend"),
    "SKILLS_LOCAL_PATH": ("decepticon.backends", "SKILLS_LOCAL_PATH"),
    "HTTPTransportSandbox": ("decepticon.backends.http_transport", "HTTPTransportSandbox"),
    "OpenShellSandbox": ("decepticon.backends.openshell_sandbox", "OpenShellSandbox"),
    "get_sandbox": ("decepticon.tools.bash.bash", "get_sandbox"),
    "set_sandbox": ("decepticon.tools.bash.bash", "set_sandbox"),
    "EngagementFilesystemBackend": (
        "decepticon.middleware.filesystem",
        "EngagementFilesystemBackend",
    ),
    "rebind_sandbox_per_run": ("decepticon.middleware.filesystem", "rebind_sandbox_per_run"),
    "workspace_from_runtime": ("decepticon.middleware.filesystem", "workspace_from_runtime"),
    "build_middleware": ("decepticon.agents.build", "build_middleware"),
    "build_tools": ("decepticon.agents.build", "build_tools"),
    "resolve_role_model": ("decepticon.agents.build", "resolve_role_model"),
    "resolve_prompt_overrides": ("decepticon.agents.build", "resolve_prompt_overrides"),
    "iter_override_bundles": ("decepticon.agents.build", "iter_override_bundles"),
    "SafetyOverrideViolation": ("decepticon.agents.build", "SafetyOverrideViolation"),
    "LLMFactory": ("decepticon.llm", "LLMFactory"),
    "ModelOverridesState": ("decepticon.middleware.model_override", "ModelOverridesState"),
    "ProxyKeyState": ("decepticon.middleware.proxy_key_override", "ProxyKeyState"),
    "SubagentTranscriptState": (
        "decepticon.middleware.subagent_transcript_state",
        "SubagentTranscriptState",
    ),
    "reduce_converging_value": ("decepticon.middleware.state_reducers", "reduce_converging_value"),
}


def test_every_export_is_covered_by_this_table() -> None:
    assert set(host.__all__) == set(REEXPORTS) | {"create_soundwave_agent"}


@pytest.mark.parametrize("name", sorted(REEXPORTS))
def test_export_is_the_framework_object(name: str) -> None:
    module, attr = REEXPORTS[name]
    assert getattr(host, name) is getattr(importlib.import_module(module), attr)


def test_former_private_paths_still_resolve_to_the_public_objects() -> None:
    filesystem = importlib.import_module("decepticon.middleware.filesystem")
    build = importlib.import_module("decepticon.agents.build")
    backends = importlib.import_module("decepticon.backends")
    assert filesystem._rebind_sandbox_per_run is host.rebind_sandbox_per_run
    assert filesystem._workspace_from_runtime is host.workspace_from_runtime
    assert build._iter_override_bundles is host.iter_override_bundles
    assert backends.HTTPSandbox is host.HTTPTransportSandbox


def test_import_does_not_build_the_soundwave_graph_until_requested() -> None:
    # A fresh interpreter: importing the Soundwave module builds its default
    # graph and LLM clients, which a host import must not trigger.
    code = (
        "import sys, decepticon_sdk.host as h\n"
        "print('soundwave' if 'decepticon.agents.standard.soundwave' in sys.modules else 'clean')\n"
        "f = h.create_soundwave_agent\n"
        "print(f.__module__ + '.' + f.__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "DECEPTICON_SKIP_BOOT": "1"},
        timeout=300,
    )
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "clean"
    assert lines[-1] == "decepticon.agents.standard.soundwave.create_soundwave_agent"


# ── Sandbox runtime ─────────────────────────────────────────────────────


def test_is_openshell_sandbox_decides_by_type_not_by_attribute() -> None:
    from decepticon.backends.factory import _UnboundOpenShellSandbox

    assert host.is_openshell_sandbox(_UnboundOpenShellSandbox())
    assert not host.is_openshell_sandbox(host.HTTPTransportSandbox("http://127.0.0.1:9"))
    # An object claiming the OpenShell authority attribute is still not one.
    assert not host.is_openshell_sandbox(SimpleNamespace(egress_authority="openshell"))


def test_http_host_routes_each_run_to_its_own_sandbox(http_host: None) -> None:
    def build(url: str, token: str) -> Any:
        return host.build_sandbox_backend(
            {"configurable": {"sandbox_url": url, "sandbox_token": token}}
        )

    a = build("http://sandbox-a.internal:9999", "token-a")
    b = build("http://sandbox-b.internal:9999", "token-b")
    assert a.id == "http-sandbox:http://sandbox-a.internal:9999"
    assert b.id == "http-sandbox:http://sandbox-b.internal:9999"
    # The same endpoint with another token is a distinct client, never shared.
    assert build("http://sandbox-a.internal:9999", "token-other") is not a
    assert build("http://sandbox-a.internal:9999", "token-a") is a


def test_openshell_host_refuses_a_provider_downgrade(
    monkeypatch: pytest.MonkeyPatch, no_resolver: None
) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    with pytest.raises(RuntimeError, match="downgrade"):
        host.build_sandbox_backend({"configurable": {"sandbox_provider": "http"}})


def test_openshell_request_needs_a_trusted_resolver_returning_an_admitted_backend(
    http_host: None, no_resolver: None
) -> None:
    config = {"configurable": {"sandbox_provider": "openshell", "thread_id": "t-1"}}
    with pytest.raises(RuntimeError, match="trusted host admission resolver"):
        host.build_sandbox_backend(config)

    # A resolver that hands back a non-OpenShell transport is refused.
    host.configure_openshell_resolver(lambda _cfg: host.HTTPTransportSandbox("http://x:1"))
    with pytest.raises(RuntimeError, match="admitted OpenShell backend"):
        host.build_sandbox_backend(config)


# ── Per-run rebinding ───────────────────────────────────────────────────


def test_rebind_swaps_only_the_workspace_default(
    http_host: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from deepagents.backends import FilesystemBackend

    monkeypatch.setenv("SANDBOX_URL", "http://shared-sidecar:9999")
    composed = host.make_agent_backend(host.build_sandbox_backend())
    runtime = SimpleNamespace(
        config={"configurable": {"sandbox_url": "http://sandbox-a.internal:9999"}},
        state={},
    )

    rebound = host.rebind_sandbox_per_run(composed, runtime)

    assert composed.default.id == "http-sandbox:http://shared-sidecar:9999"
    assert rebound.default.id == "http-sandbox:http://sandbox-a.internal:9999"
    assert set(rebound.routes) == set(composed.routes)
    # Backends that do not talk to a sandbox daemon are returned unchanged.
    plain = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    assert host.rebind_sandbox_per_run(plain, runtime) is plain


def test_workspace_from_runtime_prefers_hydrated_state() -> None:
    config = {"configurable": {"workspace_path": "/workspace/from-config"}}
    assert host.workspace_from_runtime(SimpleNamespace(config=config, state={})) == (
        "/workspace/from-config"
    )
    state = {"workspace_path": "/workspace/from-state"}
    assert host.workspace_from_runtime(SimpleNamespace(config=config, state=state)) == (
        "/workspace/from-state"
    )
    assert host.workspace_from_runtime(SimpleNamespace(config={}, state={})) is None


# ── HostRunConfigurable is what the framework actually reads ────────────

ENGAGEMENT_KEYS = {
    "engagement_name",
    "kg_engagement",
    "workspace_path",
    "language",
    "target_type",
    "target_value",
    "authorization_confirmed",
}
SANDBOX_KEYS = {"sandbox_url", "sandbox_token", "sandbox_provider"}


def _workflow_selection_keys() -> set[str]:
    from decepticon.middleware.workflow import PlanningWorkflowSelection, WorkflowSelection

    server_internal = {"workflow_default_id", "workflow_snapshot", "planning_workflow_snapshot"}
    fields = set(WorkflowSelection.model_fields) | set(PlanningWorkflowSelection.model_fields)
    return fields - server_internal


def test_declared_keys_are_exactly_the_consumed_keys() -> None:
    expected = ENGAGEMENT_KEYS | SANDBOX_KEYS | _workflow_selection_keys()
    assert set(host.HOST_RUN_CONFIGURABLE_KEYS) == expected


def test_engagement_keys_hydrate_through_a_real_graph_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langgraph.graph import END, START, StateGraph

    from decepticon.middleware.engagement import EngagementContextMiddleware

    monkeypatch.delenv("DECEPTICON_RUNTIME_ENGAGEMENT", raising=False)
    monkeypatch.delenv("BENCHMARK_MODE", raising=False)

    class State(TypedDict, total=False):
        engagement_name: str
        kg_engagement: str
        workspace_path: str
        language: str
        target_type: str
        target_value: str
        authorization_confirmed: bool

    middleware = EngagementContextMiddleware()

    def hydrate(state: State) -> dict[str, Any]:
        # The real hook, run inside a real graph so it reads the run's config.
        # It only uses ``state`` (no runtime fields), hence the casts.
        return middleware.before_agent(cast("Any", state), cast("Any", None)) or {}

    builder = StateGraph(State)
    builder.add_node("hydrate", hydrate)
    builder.add_edge(START, "hydrate")
    builder.add_edge("hydrate", END)
    graph = builder.compile()

    configurable: dict[str, Any] = {
        "engagement_name": "acme-web",
        "kg_engagement": "tenant-7--acme-web",
        "workspace_path": "/workspace/acme-web",
        "language": "ko",
        "target_type": "direct_scope",
        "target_value": "https://app.acme.example",
        "authorization_confirmed": True,
    }
    assert set(configurable) == ENGAGEMENT_KEYS

    result = graph.invoke({}, config={"configurable": configurable})

    assert {key: result.get(key) for key in ENGAGEMENT_KEYS} == configurable


def test_workflow_keys_select_through_the_runtime_models() -> None:
    from decepticon.middleware.workflow import PlanningWorkflowSelection, WorkflowSelection

    configurable = {
        "workflow_id": "red-team",
        "workflow_version": "2",
        "planning_workflow_id": "planning",
        "planning_workflow_version": "1",
        "unrelated": "ignored",
    }
    execution = WorkflowSelection.model_validate(configurable)
    planning = PlanningWorkflowSelection.model_validate(configurable)
    assert (execution.workflow_id, execution.workflow_version) == ("red-team", "2")
    assert (planning.planning_workflow_id, planning.planning_workflow_version) == (
        "planning",
        "1",
    )
