"""Host integration seam — null default, explicit override, value types."""

from __future__ import annotations

import pytest

from decepticon_core.contracts.host_integration import (
    HostModelSelection,
    HostPolicyError,
    NullHostIntegration,
    get_host_integration,
    reset_host_integration_cache,
    set_host_integration,
)


@pytest.fixture(autouse=True)
def _clean_registry():
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
