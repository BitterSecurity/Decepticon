"""Tests for the local OpenShell admission resolver.

The lifecycle tests use a fake ``SandboxClient`` injected through
``openshell_local._client`` while the policy → proto conversion runs
against the real installed SDK's proto surface, so the verified YAML-to-
``SandboxPolicy`` translation stays under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decepticon.backends import factory, openshell_local
from decepticon.backends.openshell_local import (
    _apply_enum_symbols,
    resolve_local_binding,
)
from decepticon.middleware.policy_compiler import compile_openshell_policy
from decepticon_core.types.roe import MachineEnforcement

pytest.importorskip("openshell")

ROE = {"in_scope": ["example.com", "*.staging.example.com"]}


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "plan").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plan" / "roe.json").write_text(
        json.dumps({"machine_enforcement": ROE}), encoding="utf-8"
    )
    return tmp_path


class _FakeStatus:
    def __init__(self) -> None:
        self.phase = 2
        self.current_policy_version = 1
        self.main_process_started_at_ms = 111
        self.restart_count = 0


class _FakeRef:
    def __init__(self) -> None:
        self.id = "sbx-fake-1"
        self.name = "dcpt-fake"
        self.workspace = "default"
        self.status = _FakeStatus()

    @property
    def current_policy_version(self) -> int:
        return self.status.current_policy_version


class _FakeExecResult(SimpleNamespace):
    pass


class _FakeSession:
    def __init__(self, ref: _FakeRef) -> None:
        self.id = ref.id
        self.calls: list[list[str]] = []

    def exec(self, command, **kwargs):  # noqa: ANN001, ANN003 - fake signature
        self.calls.append(list(command))
        if command[:1] == ["/bin/sh"]:
            return _FakeExecResult(exit_code=0, stdout="daemon log", stderr="")
        return _FakeExecResult(exit_code=0, stdout="", stderr="")


class _FakeClient:
    """get() fails with a not-found error until create() runs."""

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        from openshell._proto import openshell_pb2, sandbox_pb2

        self.created_spec = None
        self._exists = False
        self._sandboxes = {}
        self.config_reads = 0
        self.policy_reads = 0
        self.config_mutator = lambda response: response
        self.status_mutator = lambda response: response
        self.sessions = []

        def config(request, timeout):
            self.config_reads += 1
            ref, spec = self._sandboxes[request.name]
            return self.config_mutator(
                sandbox_pb2.GetSandboxConfigResponse(
                    policy=spec.policy,
                    version=ref.current_policy_version,
                    policy_hash="gateway-hash",
                )
            )

        def status(request, timeout):
            self.policy_reads += 1
            ref, spec = self._sandboxes[request.sandbox]
            return self.status_mutator(
                openshell_pb2.GetSandboxPolicyStatusResponse(
                    revision=openshell_pb2.SandboxPolicyRevision(
                        version=ref.current_policy_version,
                        policy=spec.policy,
                        policy_hash="gateway-hash",
                        status=openshell_pb2.POLICY_STATUS_LOADED,
                    ),
                    active_version=ref.current_policy_version,
                )
            )

        self._stub = SimpleNamespace(GetSandboxConfig=config, GetSandboxPolicyStatus=status)

    def get(self, name, workspace):  # noqa: ANN001
        if name not in self._sandboxes:
            raise RuntimeError("NOT_FOUND: sandbox does not exist")
        return self._sandboxes[name][0]

    def create(self, workspace, name, spec):  # noqa: ANN001
        assert spec.policy.network_policies["roe_in_scope"].endpoints
        assert spec.template.image
        self.created_spec = spec
        self._exists = True
        self.ref = _FakeRef()
        self.ref.id = f"sbx-fake-{len(self._sandboxes) + 1}"
        self._sandboxes[name] = (self.ref, spec)
        return self.ref

    def wait_ready(self, name, workspace, timeout_seconds):  # noqa: ANN001
        return self.get(name, workspace)

    def get_session(self, name, workspace):  # noqa: ANN001
        session = _FakeSession(self.get(name, workspace))
        self.sessions.append(session)
        return session


@pytest.fixture(autouse=True)
def _reset_cache():
    openshell_local._backends.clear()
    yield
    openshell_local._backends.clear()


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(openshell_local, "_client", lambda: client)


class TestEnumSymbols:
    def test_symbols_rewrite_recursively(self) -> None:
        mapped = _apply_enum_symbols(
            {
                "endpoints": [
                    {
                        "protocol": "rest",
                        "enforcement": "enforce",
                        "access": "full",
                        "host": "example.com",
                    }
                ],
                "landlock": {"compatibility": "best_effort"},
            }
        )
        endpoint = mapped["endpoints"][0]
        assert endpoint["protocol"] == "NETWORK_PROTOCOL_REST"
        assert endpoint["enforcement"] == "NETWORK_ENFORCEMENT_MODE_ENFORCE"
        assert endpoint["access"] == "NETWORK_ACCESS_PRESET_FULL"
        assert endpoint["host"] == "example.com"

    def test_unknown_values_pass_through(self) -> None:
        assert _apply_enum_symbols({"access": "read-write"}) == {
            "access": "NETWORK_ACCESS_PRESET_READ_WRITE"
        }
        assert _apply_enum_symbols({"tls": "skip"}) == {"tls": "skip"}


class TestResolveLocalBinding:
    def test_creates_born_confined_sandbox_and_binds_hashes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = _workspace(tmp_path)
        monkeypatch.setenv("DECEPTICON_OPENSHELL_ENDPOINT", "http://127.0.0.1:18080")
        monkeypatch.setenv("DECEPTICON_OPENSHELL_WORKSPACE", "default")
        client = _FakeClient()
        _patch_client(monkeypatch, client)

        config = {"configurable": {"workspace_path": str(workspace)}}
        backend = resolve_local_binding(config)

        assert backend.id.startswith("openshell:sbx-fake-1:")
        admission = backend.current_admission()
        compiled = compile_openshell_policy(MachineEnforcement.from_dict(ROE))
        assert admission.effective_policy_hash == compiled.canonical_hash
        assert admission.approval_hash and len(admission.approval_hash) == 64
        assert client.created_spec is not None
        # Session daemon started through the exec path (never image ENTRYPOINT).
        daemon_start = next(
            (call for call in backend._session.calls if call[:1] == ["/bin/sh"]), None
        )
        assert daemon_start is not None
        assert "decepticon.sandbox_server" in " ".join(daemon_start)

    def test_reuses_cached_backend_per_workspace_roe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = _workspace(tmp_path)
        client = _FakeClient()
        _patch_client(monkeypatch, client)
        config = {"configurable": {"workspace_path": str(workspace)}}
        first = resolve_local_binding(config)
        assert resolve_local_binding(config) is first
        assert client.created_spec is not None  # created exactly once

    def test_changed_roe_provisions_new_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = _workspace(tmp_path)
        client = _FakeClient()
        _patch_client(monkeypatch, client)
        first = resolve_local_binding({"configurable": {"workspace_path": str(workspace)}})
        (workspace / "plan" / "roe.json").write_text(
            json.dumps({"machine_enforcement": {"in_scope": ["other.example.com"]}}),
            encoding="utf-8",
        )
        second = resolve_local_binding({"configurable": {"workspace_path": str(workspace)}})
        assert first is not second
        assert first.current_admission().effective_policy_hash != (
            second.current_admission().effective_policy_hash
        )

    def test_missing_roe_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient()
        _patch_client(monkeypatch, client)
        with pytest.raises(RuntimeError, match="roe.json"):
            resolve_local_binding({"configurable": {"workspace_path": str(tmp_path)}})

    def test_uncompilable_roe_refuses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "plan").mkdir(parents=True)
        (tmp_path / "plan" / "roe.json").write_text(
            json.dumps({"machine_enforcement": {"in_scope": ["api-*.example.com"]}}),
            encoding="utf-8",
        )
        client = _FakeClient()
        _patch_client(monkeypatch, client)
        with pytest.raises(RuntimeError, match="kernel-proven"):
            resolve_local_binding({"configurable": {"workspace_path": str(tmp_path)}})
        assert client.created_spec is None

    def test_requires_workspace_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient()
        _patch_client(monkeypatch, client)
        with pytest.raises(RuntimeError, match="workspace_path"):
            resolve_local_binding({"configurable": {}})


class TestResolverInstallGating:
    def test_env_opt_in_installs_local_resolver(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factory, "_openshell_resolver", None)
        monkeypatch.setenv("DECEPTICON_OPENSHELL_LOCAL", "1")
        from decepticon.backends.openshell_local import maybe_install_local_resolver

        assert maybe_install_local_resolver() is True
        assert factory._openshell_resolver is not None

    def test_no_env_no_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factory, "_openshell_resolver", None)
        monkeypatch.delenv("DECEPTICON_OPENSHELL_LOCAL", raising=False)
        from decepticon.backends.openshell_local import maybe_install_local_resolver

        assert maybe_install_local_resolver() is False
        assert factory._openshell_resolver is None

    def test_factory_lazily_installs_on_env_opt_in(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        saved = factory._openshell_resolver
        monkeypatch.setenv("DECEPTICON_SANDBOX_PROVIDER", "openshell")
        monkeypatch.setenv("DECEPTICON_OPENSHELL_LOCAL", "1")
        try:
            # Resolver installed lazily; the binding itself then fails closed on
            # the missing engagement context instead of silently downgrading.
            with pytest.raises(RuntimeError):
                factory.build_sandbox_backend({"configurable": {"sandbox_provider": "openshell"}})
            assert factory._openshell_resolver is not None
        finally:
            factory._openshell_resolver = saved
            openshell_local._backends.clear()


@pytest.mark.parametrize(
    "failure",
    [
        "unready",
        "changed_policy",
        "pending",
        "active_version",
        "ack_hash",
        "ack_policy",
        "runtime_race",
    ],
)
def test_existing_policy_must_be_observed_loaded_before_daemon_exec(tmp_path, monkeypatch, failure):
    from openshell._proto import openshell_pb2

    from decepticon.middleware.policy_compiler import sandbox_name
    from decepticon.middleware.roe import _load_rules_for_workspace

    workspace = _workspace(tmp_path)
    compiled = compile_openshell_policy(_load_rules_for_workspace(str(workspace)))
    client = _FakeClient()
    name = sandbox_name(str(workspace), compiled.canonical_hash)
    client.create("default", name, openshell_local._policy_spec(compiled))
    if failure == "unready":
        client.ref.status.phase = openshell_pb2.SANDBOX_PHASE_UNSPECIFIED
    elif failure == "changed_policy":
        client.created_spec.policy.network_policies.clear()
    else:

        def mutate(response):
            if failure == "pending":
                response.revision.status = openshell_pb2.POLICY_STATUS_PENDING
            elif failure == "active_version":
                response.active_version += 1
            elif failure == "ack_hash":
                response.revision.policy_hash = "different-hash"
            elif failure == "ack_policy":
                response.revision.policy.network_policies.clear()
            elif failure == "runtime_race":
                client.ref.status.restart_count += 1
            return response

        client.status_mutator = mutate
    _patch_client(monkeypatch, client)
    with pytest.raises(RuntimeError):
        resolve_local_binding({"configurable": {"workspace_path": str(workspace)}})
    assert client.sessions == []  # daemon and all workloads remain unexecuted
    assert not openshell_local._backends


def test_cached_same_generation_still_reads_effective_policy(tmp_path, monkeypatch):
    from decepticon.backends.http_sandbox import SandboxError

    workspace = _workspace(tmp_path)
    client = _FakeClient()
    _patch_client(monkeypatch, client)
    backend = resolve_local_binding({"configurable": {"workspace_path": str(workspace)}})
    executed = len(backend._session.calls)
    client.created_spec.policy.network_policies.clear()
    with pytest.raises(SandboxError):
        backend.execute_tmux("must-not-execute")
    assert len(backend._session.calls) == executed


@pytest.mark.parametrize("corrupt", [False, True])
def test_actual_sdk_authenticated_loopback_policy_rpc(tmp_path, corrupt):
    """Real SDK serialization/auth/transport, with an owned loopback gateway fixture."""
    from concurrent.futures import ThreadPoolExecutor

    import grpc
    from openshell import SandboxClient
    from openshell._proto import openshell_pb2 as pb
    from openshell._proto import openshell_pb2_grpc, sandbox_pb2

    from decepticon.backends.http_sandbox import SandboxError
    from decepticon.middleware.roe import _load_rules_for_workspace

    workspace = _workspace(tmp_path)
    compiled = compile_openshell_policy(_load_rules_for_workspace(str(workspace)))
    policy = openshell_local._policy_spec(compiled).policy
    calls = []

    class Gateway(openshell_pb2_grpc.OpenShellServicer):
        def GetSandbox(self, request, context):
            assert dict(context.invocation_metadata()).get("authorization") == "Bearer fixture-only"
            assert request.workspace_scope.workspace == "default"
            calls.append("sandbox")
            response = pb.SandboxResponse()
            response.sandbox.metadata.id = "owned-sandbox"
            response.sandbox.metadata.name = "owned"
            response.sandbox.metadata.workspace = "default"
            response.sandbox.status.phase = pb.SANDBOX_PHASE_READY
            response.sandbox.status.current_policy_version = 7
            return response

        def GetSandboxConfig(self, request, context):
            assert dict(context.invocation_metadata()).get("authorization") == "Bearer fixture-only"
            assert request.name == "owned" and request.workspace_scope.workspace == "default"
            calls.append("config")
            return sandbox_pb2.GetSandboxConfigResponse(
                policy=policy, version=7, policy_hash="real-wire-hash"
            )

        def GetSandboxPolicyStatus(self, request, context):
            assert dict(context.invocation_metadata()).get("authorization") == "Bearer fixture-only"
            assert request.sandbox == "owned" and request.version == 7
            calls.append("loaded")
            return pb.GetSandboxPolicyStatusResponse(
                active_version=7,
                revision=pb.SandboxPolicyRevision(
                    policy=policy,
                    version=7,
                    policy_hash="real-wire-hash",
                    status=pb.POLICY_STATUS_PENDING if corrupt else pb.POLICY_STATUS_LOADED,
                ),
            )

    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    openshell_pb2_grpc.add_OpenShellServicer_to_server(Gateway(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = SandboxClient(f"127.0.0.1:{port}", bearer_token="fixture-only")
    try:
        if corrupt:
            with pytest.raises(SandboxError, match="acknowledgement"):
                openshell_local._verified_runtime(client, "owned", "default", compiled)
        else:
            ref = openshell_local._verified_runtime(client, "owned", "default", compiled)
            assert ref.id == "owned-sandbox" and ref.current_policy_version == 7
        assert "config" in calls and "loaded" in calls
    finally:
        client.close()
        server.stop(0).wait()
