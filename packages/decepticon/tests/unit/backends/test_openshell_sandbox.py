from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from decepticon.backends import factory
from decepticon.backends.http_sandbox import SandboxError
from decepticon.backends.openshell_sandbox import OpenShellAdmission, OpenShellSandbox


@pytest.fixture
def admitted():
    receipt = OpenShellAdmission(
        binding_id="host-owned-binding",
        sandbox_id="sandbox-a",
        workspace="engagement-a",
        runtime_generation="generation-a",
        approval_hash="approved-roe-sha",
        effective_policy_hash="gateway-canonical-policy-sha",
        expires_at=time.time() + 600,
        ready=True,
    )
    session = Mock(id="sandbox-a")
    session.exec.return_value = SimpleNamespace(
        exit_code=0,
        stdout=json.dumps({"status": 200, "body": {"output": "ok", "exit_code": 7}}),
        stderr="",
    )
    current = [receipt]
    backend = OpenShellSandbox(session, admission=receipt, current_admission=lambda: current[0])
    return backend, session, current


def test_exec_uses_only_openshell_stdio_bridge(admitted):
    backend, session, _ = admitted
    result = backend.execute("printf x; exit 7", timeout=10)
    assert result.output == "ok" and result.exit_code == 7
    args, kwargs = session.exec.call_args
    assert args[0] == ["/usr/bin/python3", "/opt/decepticon/sandbox_server/session_client.py"]
    assert json.loads(kwargs["stdin"])["body"]["command"] == "printf x; exit 7"
    assert kwargs["no_login_shell"] is True


@pytest.mark.parametrize(
    "changed",
    [
        {"ready": False},
        {"runtime_generation": "replacement"},
        {"effective_policy_hash": "changed"},
        {"approval_hash": "changed"},
        {"sandbox_id": "other-tenant"},
        {"expires_at": 0},
    ],
)
def test_changed_or_revoked_admission_never_executes(admitted, changed):
    backend, session, current = admitted
    current[0] = replace(current[0], **changed)
    with pytest.raises(SandboxError, match="admission"):
        backend.execute("must not run")
    session.exec.assert_not_called()


def test_expired_initial_admission_is_rejected(admitted):
    _, session, current = admitted
    expired = replace(current[0], expires_at=0)
    with pytest.raises(SandboxError, match="expired"):
        OpenShellSandbox(session, admission=expired, current_admission=lambda: expired)
    session.exec.assert_not_called()


def test_session_identity_is_checked_every_call(admitted):
    backend, session, _ = admitted
    session.id = "other-sandbox"
    with pytest.raises(SandboxError, match="match"):
        backend.execute("must not run")
    session.exec.assert_not_called()


def test_no_direct_http_or_policy_mutation(admitted):
    backend, session, _ = admitted
    with pytest.raises(SandboxError, match="disabled"):
        backend._http()
    with pytest.raises(SandboxError, match="control plane"):
        backend.provision_egress({})
    with pytest.raises(SandboxError, match="operation"):
        backend._request("post", "/provision_egress", json={})
    session.exec.assert_not_called()


def test_close_revokes_local_backend_without_deleting_resource(admitted):
    backend, session, _ = admitted
    backend.close()
    with pytest.raises(SandboxError):
        backend.execute("must not run")
    session.exec.assert_not_called()


def test_ambiguous_failure_is_not_retried_or_exposed(admitted):
    backend, session, _ = admitted
    session.exec.return_value = SimpleNamespace(
        exit_code=1, stdout="", stderr="synthetic-private-token"
    )
    with pytest.raises(SandboxError, match="unknown") as error:
        backend.execute("non-idempotent action")
    assert "private-token" not in str(error.value)
    assert session.exec.call_count == 1


@pytest.mark.parametrize(
    "frame", ["not JSON", '{"status":true,"body":{}}', '{"status":200,"body":[]}']
)
def test_invalid_bridge_response_is_rejected(admitted, frame):
    backend, session, _ = admitted
    session.exec.return_value.stdout = frame
    with pytest.raises(SandboxError, match="response"):
        backend.execute("printf ok")


def test_binary_and_empty_downloads_preserve_bytes(admitted):
    backend, session, _ = admitted
    session.exec.return_value.stdout = json.dumps(
        {
            "status": 200,
            "body": {
                "files": [
                    {"path": "/workspace/empty", "data_b64": "", "error": None},
                    {
                        "path": "/workspace/binary",
                        "data_b64": base64.b64encode(b"\x00\xff").decode(),
                        "error": None,
                    },
                ]
            },
        }
    )
    assert [
        f.content for f in backend.download_files(["/workspace/empty", "/workspace/binary"])
    ] == [b"", b"\x00\xff"]


def test_jobs_are_isolated_between_bindings(admitted):
    a, session, current = admitted
    b = OpenShellSandbox(session, admission=current[0], current_admission=lambda: current[0])
    a._jobs.register("main", "sleep 1", 0)
    assert b._jobs.all_jobs() == []


def test_openshell_selection_cannot_fall_back_to_http(monkeypatch):
    monkeypatch.setattr(factory, "_openshell_resolver", None)
    with pytest.raises(RuntimeError, match="trusted host"):
        factory.build_sandbox_backend(
            {"configurable": {"sandbox_provider": "openshell", "sandbox_url": "http://wrong"}}
        )


def test_bash_and_files_resolve_same_host_binding(admitted, monkeypatch):
    from decepticon.tools.bash.bash import get_sandbox

    backend, _, _ = admitted
    config = {
        "configurable": {"sandbox_provider": "openshell", "openshell_binding_id": "opaque-ref"}
    }
    resolver = Mock(return_value=backend)
    monkeypatch.setattr(factory, "_openshell_resolver", resolver)
    assert factory.build_sandbox_backend(config) is backend
    assert get_sandbox(config) is backend
    assert resolver.call_args.args[0] is config
