"""First-run journey: boot must not crash-loop before a scope exists.

With the OpenShell provider enabled, graph construction runs before any
engagement scope is materialized. The binding refuses to admit a workspace
without ``plan/roe.json`` — that refusal must degrade to the unbound
placeholder at graph-construction time (no run) and keep failing loudly for
real runs, so the operator journey is boot → create engagement → ``/scope
import`` → work instead of a crash loop on first boot (observed on the OSS
verification stack).
"""

from __future__ import annotations

import pytest

from decepticon.backends import factory


def _resolver_missing_scope(run_config):  # pragma: no cover - trivial
    raise RuntimeError("OpenShell binding for /workspace requires a materialized plan/roe.json")


@pytest.fixture(autouse=True)
def _reset_resolver(monkeypatch: pytest.MonkeyPatch):
    yield
    monkeypatch.setattr(factory, "_openshell_resolver", None)


def test_first_run_boots_unbound_without_scope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    factory.configure_openshell_resolver(_resolver_missing_scope)
    backend = factory.build_sandbox_backend({"configurable": {}})
    assert type(backend).__name__ == "_UnboundOpenShellSandbox"
    assert backend.id == "openshell:unbound"


def test_real_run_without_scope_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    factory.configure_openshell_resolver(_resolver_missing_scope)
    with pytest.raises(RuntimeError, match="materialized plan/roe.json"):
        factory.build_sandbox_backend(
            {"configurable": {"thread_id": "t1", "workspace_path": "/workspace"}}
        )


def test_other_resolver_errors_still_raise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")

    def resolver_changed(run_config):
        raise RuntimeError("OpenShell approved RoE changed")

    factory.configure_openshell_resolver(resolver_changed)
    with pytest.raises(RuntimeError, match="approved RoE changed"):
        factory.build_sandbox_backend({"configurable": {}})


def test_unbound_reads_report_empty_workspace():
    """Pre-admission reads must see an empty workspace, not a crash."""
    monkeypatch_env = pytest.MonkeyPatch()
    monkeypatch_env.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    factory.configure_openshell_resolver(_resolver_missing_scope)
    backend = factory.build_sandbox_backend({"configurable": {}})

    read = backend.read("/workspace/plan/opplan.json")
    assert read.error and "file_not_found" in read.error and read.file_data is None
    assert backend.ls("/workspace").entries == []
    assert backend.glob("/workspace/**/*.json").matches == []
    assert backend.grep("secret", "/workspace").matches == []

    # The loud path stays loud: writes and execution still refuse.
    with pytest.raises(RuntimeError, match="run binding is required"):
        backend._request("post", "/execute", json={})

    monkeypatch_env.undo()


def test_ambient_default_config_does_not_admit_a_session(monkeypatch):
    from langchain_core.runnables.config import ensure_config, var_child_runnable_config

    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")

    def unexpected(_config):
        pytest.fail("Graph import must not resolve an execution session")

    factory.configure_openshell_resolver(unexpected)
    token = var_child_runnable_config.set(ensure_config())
    try:
        backend = factory.build_sandbox_backend()
        assert backend.id == "openshell:unbound"
        with pytest.raises(RuntimeError, match="run binding is required"):
            backend.execute("must not run")
        with pytest.raises(RuntimeError, match="direct HTTP"):
            backend._http()
    finally:
        var_child_runnable_config.reset(token)


@pytest.mark.parametrize("scope", [
    {"thread_id": "thread-without-authority"},
    {"native_runtime_grant": "forged-grant"},
    {"organization_id": "org-without-authority"},
    {"sandbox_provider": "openshell"},
])
def test_ambient_execution_config_still_requires_authority(monkeypatch, scope):
    from langchain_core.runnables.config import ensure_config, var_child_runnable_config

    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")

    def deny(config):
        assert config["configurable"] == scope
        raise RuntimeError("Current native session authority is unavailable")

    factory.configure_openshell_resolver(deny)
    token = var_child_runnable_config.set(ensure_config({"configurable": scope}))
    try:
        with pytest.raises(RuntimeError, match="native session authority"):
            factory.build_sandbox_backend()
    finally:
        var_child_runnable_config.reset(token)


@pytest.mark.parametrize("config", [
    {"configurable": {}},
    {"run_id": "identified-run", "configurable": {}},
    {"unknown_execution_field": True, "configurable": {}},
])
def test_explicit_or_identified_config_is_not_normalized(monkeypatch, config):
    from langchain_core.runnables.config import var_child_runnable_config

    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")

    def deny(received):
        assert received == config
        raise RuntimeError("Current native session authority is unavailable")

    factory.configure_openshell_resolver(deny)
    token = var_child_runnable_config.set(config)
    try:
        with pytest.raises(RuntimeError, match="native session authority"):
            factory.build_sandbox_backend(config)
        if set(config) != {"configurable"}:
            with pytest.raises(RuntimeError, match="native session authority"):
                factory.build_sandbox_backend()
    finally:
        var_child_runnable_config.reset(token)
