"""Host integration seam — null default, explicit override, value types."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from decepticon_core.contracts import host_integration as registry
from decepticon_core.contracts.host_integration import (
    HostModelSelection,
    HostPolicyError,
    NullHostIntegration,
    get_host_integration,
    reset_host_integration_cache,
    set_host_integration,
)


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.delenv("DECEPTICON_HOST_INTEGRATION", raising=False)
    monkeypatch.setattr(registry, "entry_points", lambda **_: [])
    set_host_integration(None)
    reset_host_integration_cache()
    yield
    set_host_integration(None)
    reset_host_integration_cache()


def test_default_is_null_and_preserves_oss_behavior():
    host = get_host_integration()
    assert isinstance(host, NullHostIntegration)
    # The two paths core takes without guarding on read_policy():
    assert host.read_policy() is None
    assert host.policy_tools("recon") == []
    assert host.server_routes() == []


def test_policy_taking_methods_never_reached_but_fail_closed_if_called():
    # Consumers guard on read_policy() is not None; a direct call on the null
    # provider is a programming error and raises rather than silently allowing.
    host = NullHostIntegration()
    for call in (
        lambda: host.require_current(object()),
        lambda: host.model_selection(object(), "recon"),
        lambda: host.mcp_list_connections(object()),
        lambda: host.mcp_call(object(), "c", "t", "{}"),
    ):
        with pytest.raises(HostPolicyError):
            call()


def test_explicit_override_then_reset():
    class FakeHost(NullHostIntegration):
        def read_policy(self):
            return {"revision": 1}

    set_host_integration(FakeHost())
    assert get_host_integration().read_policy() == {"revision": 1}
    set_host_integration(None)
    assert get_host_integration().read_policy() is None


def test_model_selection_value_type():
    sel = HostModelSelection(model="openai/gpt-5.6", effort="high")
    assert sel.model == "openai/gpt-5.6"
    assert sel.effort == "high"
    assert HostModelSelection(model="x").effort is None


def test_host_policy_error_carries_code():
    err = HostPolicyError("policy_changed")
    assert err.code == "policy_changed"
    assert str(err) == "policy_changed"


def _entry(name, provider):
    return SimpleNamespace(name=name, load=lambda: provider)


def test_broken_installed_provider_refuses_without_logging_secret(monkeypatch, caplog):
    def broken():
        raise ImportError("owned-secret-must-not-appear")

    monkeypatch.setattr(registry, "entry_points", lambda **_: [_entry("vendor", broken)])
    with pytest.raises(HostPolicyError, match="host_integration_load_failed") as failure:
        get_host_integration()
    assert "owned-secret" not in str(failure.value)
    assert "owned-secret" not in caplog.text


@pytest.mark.parametrize("required", [False, True])
def test_metadata_failure_never_selects_null(monkeypatch, required):
    if required:
        monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "vendor")

    def broken(**_):
        raise OSError("owned metadata failure")

    monkeypatch.setattr(registry, "entry_points", broken)
    with pytest.raises(HostPolicyError, match="host_integration_discovery_failed"):
        get_host_integration()


@pytest.mark.parametrize("entries", [[], [_entry("other", NullHostIntegration())]])
def test_required_provider_absence_refuses(monkeypatch, entries):
    monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "vendor")
    monkeypatch.setattr(registry, "entry_points", lambda **_: entries)
    with pytest.raises(HostPolicyError, match="required_host_integration_unavailable"):
        get_host_integration()


def test_explicit_selection_does_not_load_other_installed_providers(monkeypatch):
    chosen = NullHostIntegration()

    def other():
        raise AssertionError("Unselected provider must not load")

    monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "vendor")
    monkeypatch.setattr(
        registry,
        "entry_points",
        lambda **_: [
            _entry("other", other),
            _entry("vendor", chosen),
        ],
    )
    assert get_host_integration() is chosen


@pytest.mark.parametrize("required", [False, True])
def test_duplicate_enabled_providers_refuse_independently_of_order(monkeypatch, required):
    if required:
        monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "vendor")
    providers = [_entry("vendor", NullHostIntegration()), _entry("vendor", NullHostIntegration())]
    monkeypatch.setattr(registry, "entry_points", lambda **_: providers)
    with pytest.raises(
        HostPolicyError,
        match="required_host_integration_unavailable" if required else "ambiguous_host_integration",
    ):
        get_host_integration()
    providers.reverse()
    with pytest.raises(HostPolicyError):
        get_host_integration()


@pytest.mark.parametrize("required", [False, True])
def test_allowlist_disabled_host_is_null_only_when_not_required(monkeypatch, required):
    class Vendor(NullHostIntegration):
        bundle = "vendor"

    monkeypatch.setattr(registry, "entry_points", lambda **_: [_entry("vendor", Vendor)])
    monkeypatch.setattr(registry, "is_bundle_enabled", lambda _: False)
    if required:
        monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "vendor")
        with pytest.raises(HostPolicyError, match="required_host_integration_disabled"):
            get_host_integration()
    else:
        assert type(get_host_integration()) is NullHostIntegration


@pytest.mark.parametrize("provider", [object(), SimpleNamespace(read_policy=lambda: None)])
def test_incomplete_provider_never_becomes_authority(monkeypatch, provider):
    monkeypatch.setattr(registry, "entry_points", lambda **_: [_entry("vendor", provider)])
    with pytest.raises(HostPolicyError, match="invalid_host_integration_provider"):
        get_host_integration()
    with pytest.raises(HostPolicyError, match="invalid_host_integration_provider"):
        set_host_integration(provider)


def test_falsey_valid_provider_is_not_replaced_by_null(monkeypatch):
    class Vendor(NullHostIntegration):
        def __bool__(self):
            return False

    provider = Vendor()
    monkeypatch.setattr(registry, "entry_points", lambda **_: [_entry("vendor", provider)])
    assert get_host_integration() is provider


def test_discovery_initializes_one_provider_under_concurrent_startup(monkeypatch):
    calls = []

    class Vendor(NullHostIntegration):
        def __init__(self):
            calls.append(True)

    monkeypatch.setattr(registry, "entry_points", lambda **_: [_entry("vendor", Vendor)])
    with ThreadPoolExecutor(max_workers=8) as executor:
        providers = list(executor.map(lambda _: get_host_integration(), range(32)))
    assert calls == [True]
    assert all(provider is providers[0] for provider in providers)


@pytest.mark.parametrize("name", ["vendor/name", "x" * 129, "vendor\nsecret"])
def test_invalid_required_selection_refuses_before_discovery(monkeypatch, name):
    monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", name)
    with pytest.raises(HostPolicyError, match="invalid_host_integration_selection"):
        get_host_integration()


def test_discovery_failure_is_not_cached_as_no_host(monkeypatch):
    entry = _entry("vendor", object())
    monkeypatch.setattr(registry, "entry_points", lambda **_: [entry])
    with pytest.raises(HostPolicyError):
        get_host_integration()
    provider = NullHostIntegration()
    entry.load = lambda: provider
    assert get_host_integration() is provider
