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
