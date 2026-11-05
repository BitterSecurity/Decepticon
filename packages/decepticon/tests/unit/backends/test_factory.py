"""Tests for ``decepticon.backends.factory.build_sandbox_backend``."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from decepticon.backends import build_sandbox_backend
from decepticon.backends.factory import _resolve_endpoint, _shared_sandbox


@pytest.fixture(autouse=True)
def _clear_shared_sandbox_cache() -> Iterator[None]:
    """The shared cache survives across the process, so isolate tests."""
    _shared_sandbox.cache_clear()
    yield
    _shared_sandbox.cache_clear()


def test_build_sandbox_backend_returns_shared_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "http")
    """Repeated calls with the same env must return the *same* HTTPSandbox.

    Why this matters: ``langgraph dev`` invokes one factory per registered
    graph at startup. Without a shared instance every factory builds its
    own ``HTTPSandbox`` + ``BackgroundJobTracker``, and the
    ``SandboxNotificationMiddleware`` instance held by each graph sees a
    different ``_jobs`` view than the bash tool actually registers
    against — completion notifications never reach the agent.
    """
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox:9999")
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)

    a = build_sandbox_backend()
    b = build_sandbox_backend()

    assert a is b
    assert a._jobs is b._jobs


def test_build_sandbox_backend_keys_on_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct base URLs must produce distinct instances.

    Multi-tenant pools may target different daemons in the same
    process; the cache key must respect that.
    """
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-a:9999")
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    a = build_sandbox_backend()

    monkeypatch.setenv("SANDBOX_URL", "http://sandbox-b:9999")
    b = build_sandbox_backend()

    assert a is not b


def test_build_sandbox_backend_keys_on_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Distinct tokens against the same URL still produce distinct instances."""
    monkeypatch.setenv("SANDBOX_URL", "http://sandbox:9999")

    monkeypatch.setenv("SANDBOX_TOKEN", "tenant-a")
    a = build_sandbox_backend()

    monkeypatch.setenv("SANDBOX_TOKEN", "tenant-b")
    b = build_sandbox_backend()

    assert a is not b


# ── per-run config routing ────────────────────────────────────────────────


def _patch_get_config(
    monkeypatch: pytest.MonkeyPatch, value: object, *, raises: bool = False
) -> None:
    """Patch ``langgraph.config.get_config`` (imported lazily inside factory)."""
    import langgraph.config as lgc

    def fake() -> object:
        if raises:
            raise RuntimeError("Called get_config outside of a runnable context")
        return value

    monkeypatch.setattr(lgc, "get_config", fake)


def test_resolve_endpoint_prefers_run_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shared process routes by the current run's configurable, not env."""
    monkeypatch.setenv("SANDBOX_URL", "http://env-sandbox:9999")
    monkeypatch.setenv("SANDBOX_TOKEN", "env-token")
    _patch_get_config(
        monkeypatch,
        {"configurable": {"sandbox_url": "http://eng-42:9999", "sandbox_token": "tok-42"}},
    )

    assert _resolve_endpoint() == ("http://eng-42:9999", "tok-42")


def test_resolve_endpoint_falls_back_to_env_outside_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No runnable context (import-time / sidecar / dev) means env wins."""
    monkeypatch.setenv("SANDBOX_URL", "http://env-sandbox:9999")
    monkeypatch.setenv("SANDBOX_TOKEN", "env-token")
    _patch_get_config(monkeypatch, None, raises=True)

    assert _resolve_endpoint() == ("http://env-sandbox:9999", "env-token")


def test_resolve_endpoint_env_when_config_lacks_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run config without sandbox_* keys falls back to env per-field."""
    monkeypatch.setenv("SANDBOX_URL", "http://env-sandbox:9999")
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    _patch_get_config(monkeypatch, {"configurable": {"thread_id": "t1"}})

    assert _resolve_endpoint() == ("http://env-sandbox:9999", None)


def test_resolve_endpoint_default_url_when_nothing_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No config, no env means loopback default."""
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)
    _patch_get_config(monkeypatch, None, raises=True)

    assert _resolve_endpoint() == ("http://localhost:9999", None)


def test_build_sandbox_backend_routes_per_run_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "http")
    """Two runs with distinct configurable sandboxes get distinct instances."""
    monkeypatch.delenv("SANDBOX_URL", raising=False)
    monkeypatch.delenv("SANDBOX_TOKEN", raising=False)

    _patch_get_config(
        monkeypatch,
        {"configurable": {"sandbox_url": "http://eng-a:9999", "sandbox_token": "a"}},
    )
    a = build_sandbox_backend()

    _patch_get_config(
        monkeypatch,
        {"configurable": {"sandbox_url": "http://eng-b:9999", "sandbox_token": "b"}},
    )
    b = build_sandbox_backend()

    assert a is not b
    assert a._base_url != b._base_url


def test_build_sandbox_backend_reads_configurable_inside_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "http")
    """A real LangGraph run should route the factory from run config."""
    from langgraph.graph import END, START, StateGraph

    monkeypatch.setenv("SANDBOX_URL", "http://env-sandbox:9999")
    monkeypatch.setenv("SANDBOX_TOKEN", "env-token")

    seen: dict[str, str | None] = {}

    def node(state: dict) -> dict:
        sb = build_sandbox_backend()
        seen["url"] = sb._base_url
        seen["token"] = sb._token
        return {}

    graph = StateGraph(dict).add_node("n", node).add_edge(START, "n").add_edge("n", END).compile()

    graph.invoke(
        {},
        {"configurable": {"sandbox_url": "http://eng-99:9999", "sandbox_token": "tok-99"}},
    )

    assert seen["url"] == "http://eng-99:9999"
    assert seen["token"] == "tok-99"
