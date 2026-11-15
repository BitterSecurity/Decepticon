from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from decepticon.backends import factory
from decepticon.backends.http_transport import SandboxError
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
    assert args[0] == [
        "/usr/bin/python3",
        "-I",
        "/usr/local/lib/decepticon-sandbox/decepticon_sandbox/server/session_client.py",
    ]
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


def test_expired_admission_renews_through_trusted_host():
    # A long-lived daemon holds the graph-load backend across the host's
    # admission window: the runtime identity is unchanged but the receipt
    # TTL passed. With a renew callable wired, the expiry re-reads the
    # receipt through the trusted host instead of discarding the run.
    receipt = OpenShellAdmission(
        binding_id="host-owned-binding",
        sandbox_id="sandbox-a",
        workspace="engagement-a",
        runtime_generation="generation-a",
        approval_hash="approved-roe-sha",
        effective_policy_hash="gateway-canonical-policy-sha",
        expires_at=time.time() - 10,
        ready=True,
    )
    renewed = replace(receipt, expires_at=time.time() + 600)
    current = [receipt]
    renew_calls: list[int] = []

    def renew():
        renew_calls.append(1)
        current[0] = renewed
        return renewed

    session = Mock(id="sandbox-a")
    session.exec.return_value = SimpleNamespace(
        exit_code=0,
        stdout=json.dumps({"status": 200, "body": {"output": "ok", "exit_code": 0}}),
        stderr="",
    )
    backend = OpenShellSandbox(
        session,
        admission=receipt,
        current_admission=lambda: current[0],
        renew_admission=renew,
    )
    assert current[0] is renewed
    backend.execute("echo hi", timeout=5)
    assert len(renew_calls) == 1
    session.exec.assert_called_once()


def test_expired_admission_without_renew_still_fails_closed():
    receipt = OpenShellAdmission(
        binding_id="host-owned-binding",
        sandbox_id="sandbox-a",
        workspace="engagement-a",
        runtime_generation="generation-a",
        approval_hash="approved-roe-sha",
        effective_policy_hash="gateway-canonical-policy-sha",
        expires_at=time.time() - 10,
        ready=True,
    )
    session = Mock(id="sandbox-a")
    with pytest.raises(SandboxError, match="expired"):
        OpenShellSandbox(
            session,
            admission=receipt,
            current_admission=lambda: receipt,
            renew_admission=None,
        )
    session.exec.assert_not_called()


def test_renewal_failure_fails_closed():
    receipt = OpenShellAdmission(
        binding_id="host-owned-binding",
        sandbox_id="sandbox-a",
        workspace="engagement-a",
        runtime_generation="generation-a",
        approval_hash="approved-roe-sha",
        effective_policy_hash="gateway-canonical-policy-sha",
        expires_at=time.time() - 10,
        ready=True,
    )

    def renew():
        raise SandboxError("OpenShell admission source is unavailable")

    session = Mock(id="sandbox-a")
    with pytest.raises(SandboxError, match="unavailable"):
        OpenShellSandbox(
            session,
            admission=receipt,
            current_admission=lambda: receipt,
            renew_admission=renew,
        )
    session.exec.assert_not_called()


def test_acknowledged_successor_preserves_backend_and_background_jobs(admitted):
    _, session, current = admitted
    original = current[0]
    renew = Mock(side_effect=lambda: current[0])
    backend = OpenShellSandbox(
        session, admission=original, current_admission=lambda: current[0], renew_admission=renew
    )
    backend._jobs.register("main", "sleep 1", 0)
    jobs = backend._jobs.all_jobs()
    identity = backend.id
    current[0] = replace(original, expires_at=original.expires_at + 60)
    backend.execute("printf first")
    backend.execute("printf second")
    renew.assert_called_once_with()
    assert backend.id == identity
    assert backend._jobs.all_jobs() == jobs
    assert backend.current_admission() == current[0]
    assert session.exec.call_count == 2


def test_successor_expiry_without_explicit_callback_is_refused(admitted):
    backend, session, current = admitted
    current[0] = replace(current[0], expires_at=current[0].expires_at + 60)
    with pytest.raises(SandboxError, match="admission"):
        backend.execute("must not run")
    session.exec.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"binding_id": "foreign-binding"},
        {"sandbox_id": "foreign-sandbox"},
        {"workspace": "foreign-workspace"},
        {"runtime_generation": "replacement"},
        {"approval_hash": "replacement"},
        {"effective_policy_hash": "replacement"},
        {"ready": False},
        {"expires_at": float("nan")},
        {"expires_at": float("inf")},
    ],
)
def test_expired_receipt_renewal_cannot_replace_identity_or_policy(admitted, monkeypatch, change):
    _, session, current = admitted
    original = current[0]
    renewed = replace(
        original,
        expires_at=original.expires_at + 600,
        **{key: value for key, value in change.items() if key != "expires_at"},
    )
    if "expires_at" in change:
        renewed = replace(renewed, expires_at=change["expires_at"])

    def renew():
        current[0] = renewed
        return renewed

    callback = Mock(side_effect=renew)
    backend = OpenShellSandbox(
        session, admission=original, current_admission=lambda: current[0], renew_admission=callback
    )
    monkeypatch.setattr(time, "time", lambda: original.expires_at + 1)
    with pytest.raises(SandboxError, match="renewal is unavailable"):
        backend.execute("must not run")
    with pytest.raises(SandboxError, match="admission"):
        backend.execute("must not replay renewal")
    assert backend._admission == original
    callback.assert_called_once_with()
    session.exec.assert_not_called()


def test_unobserved_renewal_does_not_commit_or_replay_and_hides_callback_error(
    admitted, monkeypatch
):
    _, session, current = admitted
    original = current[0]
    callback = Mock(side_effect=RuntimeError("synthetic-private-credential"))
    backend = OpenShellSandbox(
        session, admission=original, current_admission=lambda: current[0], renew_admission=callback
    )
    monkeypatch.setattr(time, "time", lambda: original.expires_at + 1)
    with pytest.raises(SandboxError, match="unavailable") as error:
        backend.execute("must not run")
    assert "private-credential" not in str(error.value)
    with pytest.raises(SandboxError):
        backend.execute("must not replay renewal")
    callback.assert_called_once_with()
    assert backend._admission == original
    session.exec.assert_not_called()


def test_renewal_requires_an_independent_current_receipt_observation(admitted):
    _, session, current = admitted
    original = current[0]
    successor = replace(original, expires_at=original.expires_at + 60)
    unobserved = replace(successor, expires_at=successor.expires_at + 60)
    callback = Mock(return_value=unobserved)
    backend = OpenShellSandbox(
        session, admission=original, current_admission=lambda: current[0], renew_admission=callback
    )
    current[0] = successor
    with pytest.raises(SandboxError, match="unavailable"):
        backend.execute("must not run")
    assert backend._admission == original
    callback.assert_called_once_with()
    session.exec.assert_not_called()


def test_parallel_callers_share_one_acknowledged_successor(admitted):
    _, session, current = admitted
    original = current[0]
    entered, release, second_started, second_read = (threading.Event() for _ in range(4))

    def read():
        if threading.current_thread().name.endswith("_1"):
            second_read.set()
        return current[0]

    def renew():
        entered.set()
        assert release.wait(2), "Owned renewal fixture was not released"
        return current[0]

    callback = Mock(side_effect=renew)
    backend = OpenShellSandbox(
        session, admission=original, current_admission=read, renew_admission=callback
    )
    current[0] = replace(original, expires_at=original.expires_at + 60)

    def second():
        second_started.set()
        return backend.execute("printf second")

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="owned-renewal") as pool:
        first = pool.submit(backend.execute, "printf first")
        try:
            assert entered.wait(2)
            other = pool.submit(second)
            assert second_started.wait(2)
            assert not second_read.wait(0.05)
        finally:
            release.set()
        assert first.result(timeout=2).output == "ok"
        assert other.result(timeout=2).output == "ok"
    callback.assert_called_once_with()
    assert session.exec.call_count == 2


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
    monkeypatch.setenv("DECEPTICON_OPENSHELL_LOCAL", "0")  # no local resolver wired
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


def test_host_provider_cannot_be_downgraded_by_run_config(admitted, monkeypatch):
    backend, _, _ = admitted
    resolver = Mock(return_value=backend)
    monkeypatch.setattr(factory, "_openshell_resolver", resolver)
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    with pytest.raises(RuntimeError, match="downgrade"):
        factory.build_sandbox_backend(
            {"configurable": {"sandbox_provider": "http", "sandbox_url": "http://wrong"}}
        )
    resolver.assert_not_called()


def test_host_provider_intercepts_the_legacy_bash_url_shortcut(admitted, monkeypatch):
    from decepticon.tools.bash.bash import get_sandbox

    backend, session, _ = admitted
    monkeypatch.setattr(factory, "_openshell_resolver", lambda _config: backend)
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    config = {
        "configurable": {"sandbox_url": "http://wrong", "openshell_binding_id": "host-binding"}
    }
    assert get_sandbox(config) is backend
    session.exec.assert_not_called()


def test_static_graph_placeholder_never_executes_or_opens_http(admitted, monkeypatch):
    backend, session, _ = admitted
    monkeypatch.setattr(factory, "_openshell_resolver", lambda _config: backend)
    monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
    placeholder = factory.build_sandbox_backend()
    assert placeholder.id == "openshell:unbound"
    with pytest.raises(RuntimeError, match="binding"):
        placeholder.execute("must not run")
    with pytest.raises(RuntimeError, match="direct HTTP"):
        placeholder._http()
    session.exec.assert_not_called()
