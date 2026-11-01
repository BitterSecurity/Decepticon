"""Admission uses the real policy compiler and no external sandbox/network."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from decepticon.middleware import roe
from decepticon_core.types.roe import MachineEnforcement


@pytest.fixture
def backend(monkeypatch):
    rules = MachineEnforcement.from_dict({"in_scope": [{"target": "192.0.2.0/24", "type": "cidr"}]})
    monkeypatch.setattr(roe, "_egress_disabled", lambda: False)
    monkeypatch.setattr(roe, "_load_rules_for_workspace", lambda _: rules)
    sandbox = Mock()
    sandbox.provision_egress.return_value = {"applied": True}
    monkeypatch.setattr("decepticon.backends.factory.build_sandbox_backend", lambda: sandbox)
    return sandbox


@pytest.mark.parametrize("result", [{"applied": False}, {}, None, {"applied": "true"}])
@pytest.mark.parametrize("async_hook", [False, True])
def test_unconfirmed_policy_refuses_admission_and_retries(backend, result, async_hook):
    backend.provision_egress.return_value = result
    guard = roe.RoEGuardrailMiddleware()
    state = {"workspace_path": "/workspace/isolated"}
    with pytest.raises(roe.EgressProvisioningError, match="execution was refused"):
        if async_hook:
            asyncio.run(guard.abefore_agent(state, None))
        else:
            guard.before_agent(state, None)
    backend.provision_egress.return_value = {"applied": True}
    guard.before_agent(state, None)
    guard.before_agent(state, None)
    assert backend.provision_egress.call_count == 2
    assert backend.provision_egress.call_args.args[0].enforce is True


def test_transport_failure_refuses_admission_without_exposing_backend_details(backend):
    backend.provision_egress.side_effect = OSError("private-backend-detail")
    guard = roe.RoEGuardrailMiddleware()
    for _ in range(2):
        with pytest.raises(roe.EgressProvisioningError) as error:
            guard.before_agent({"workspace_path": "/workspace/isolated"}, None)
        assert "private-backend-detail" not in str(error.value)
    assert backend.provision_egress.call_count == 2


def test_inflight_policy_is_not_treated_as_confirmed(backend):
    entered, release = threading.Event(), threading.Event()
    second_started = threading.Event()
    second_returned = threading.Event()

    def provision(_policy):
        entered.set()
        assert release.wait(5)
        return {"applied": True}

    backend.provision_egress.side_effect = provision
    guard = roe.RoEGuardrailMiddleware()
    state = {"workspace_path": "/workspace/isolated"}

    def second():
        second_started.set()
        guard.before_agent(state, None)
        second_returned.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(guard.before_agent, state, None)
        assert entered.wait(5)
        waiting = pool.submit(second)
        assert second_started.wait(5)
        try:
            assert not second_returned.wait(0.1)
        finally:
            release.set()
        first.result(timeout=5)
        waiting.result(timeout=5)
    backend.provision_egress.assert_called_once()


def test_explicit_operator_opt_out_preserves_existing_contract(backend, monkeypatch):
    monkeypatch.setattr(roe, "_egress_disabled", lambda: True)
    roe.RoEGuardrailMiddleware().before_agent({"workspace_path": "/workspace/isolated"}, None)
    backend.provision_egress.assert_not_called()
