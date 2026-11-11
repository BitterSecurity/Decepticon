"""A failed required host must not reach ordinary model/tool composition."""

from types import SimpleNamespace

import pytest

from decepticon.agents.build import build_tools, resolve_role_model
from decepticon.middleware.model_override import ModelOverrideMiddleware
from decepticon.middleware.run_controls import _must_propagate
from decepticon_core.contracts import host_integration as registry


@pytest.mark.parametrize("failure", ["missing", "broken", "duplicate"])
@pytest.mark.parametrize("consumer", ["model", "tools", "middleware"])
def test_required_host_failure_refuses_real_engine_consumers(monkeypatch, failure, consumer):
    registry.set_host_integration(None)
    registry.reset_host_integration_cache()
    monkeypatch.setenv("DECEPTICON_HOST_INTEGRATION", "owned-host")

    def broken():
        raise ImportError("Owned provider fixture is unavailable")

    entry = SimpleNamespace(name="owned-host", load=broken)
    entries = {"missing": [], "broken": [entry], "duplicate": [entry, entry]}[failure]
    monkeypatch.setattr(registry, "entry_points", lambda **_: entries)
    try:
        with pytest.raises(registry.HostPolicyError) as refusal:
            if consumer == "model":
                resolve_role_model("recon")
            elif consumer == "tools":
                build_tools(role="recon", standard_tools=[])
            else:
                ModelOverrideMiddleware(role="recon")
        assert _must_propagate(refusal.value)
    finally:
        registry.set_host_integration(None)
        registry.reset_host_integration_cache()
