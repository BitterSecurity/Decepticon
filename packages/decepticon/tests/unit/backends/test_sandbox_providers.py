"""Sandbox provider selection (backends/providers.py) and factory dispatch."""

from __future__ import annotations

from importlib.metadata import EntryPoint

import pytest

from decepticon.backends import factory, providers


def test_default_provider_is_openshell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECEPTICON_SANDBOX_PROVIDER", raising=False)
    assert providers.host_provider() == "openshell"
    assert providers.uses_openshell({})


def test_docker_is_a_builtin_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "Docker ")
    assert providers.host_provider() == "docker"
    assert not providers.uses_openshell({})
    assert providers.uses_openshell({"sandbox_provider": "openshell"})


def test_http_is_a_deprecated_alias_of_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "http")
    with pytest.warns(DeprecationWarning, match="'http' is deprecated"):
        assert providers.host_provider() == "docker"
    with pytest.warns(DeprecationWarning):
        assert providers.requested_provider({"sandbox_provider": "http"}) == "docker"


def test_unknown_provider_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "nope")
    with pytest.raises(providers.UnknownSandboxProviderError, match="available"):
        providers.host_provider()


class _PluginBackend:
    def __init__(self, config: dict) -> None:
        self.config = config


def _plugin_factory(config: dict) -> _PluginBackend:
    return _PluginBackend(config)


def _entry_points(*, group: str):
    assert group == providers.ENTRY_POINT_GROUP
    return [EntryPoint(name="hosted", value=f"{__name__}:_plugin_factory", group=group)]


def test_plugin_provider_is_selected_by_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(providers, "entry_points", _entry_points)
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "hosted")
    assert "hosted" in providers.available_providers()
    config = {"configurable": {"workspace_path": "/workspace/eng", "thread_id": "t"}}
    backend = factory.build_sandbox_backend(config)
    assert isinstance(backend, _PluginBackend)
    assert backend.config["configurable"]["workspace_path"] == "/workspace/eng"


def test_openshell_host_still_refuses_a_run_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DECEPTICON_SANDBOX_PROVIDER", raising=False)
    with pytest.raises(RuntimeError, match="downgrade"):
        factory.build_sandbox_backend({"configurable": {"sandbox_provider": "docker"}})


def test_bash_uses_the_openshell_binding_when_the_env_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: bash treated an unset provider as non-OpenShell while the
    factory treated it as OpenShell, so bash fell back to the unbound process
    default instead of the run's admitted binding."""
    from decepticon.tools.bash.bash import get_sandbox

    monkeypatch.delenv("DECEPTICON_SANDBOX_PROVIDER", raising=False)
    sentinel = object()
    seen: list[dict] = []

    def fake_build(config=None):
        seen.append(config)
        return sentinel

    monkeypatch.setattr(factory, "build_sandbox_backend", fake_build)
    config = {"configurable": {"workspace_path": "/workspace/eng", "thread_id": "t"}}
    assert get_sandbox(config) is sentinel
    assert seen == [config]
