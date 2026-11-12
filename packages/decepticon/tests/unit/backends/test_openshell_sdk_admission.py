"""Selected SDK wire contracts; loopback fixture is not kernel confinement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from decepticon_sandbox.server.session_client import MANAGED_BRIDGE, MANAGED_START

from decepticon.backends import openshell_local
from decepticon.backends.http_transport import SandboxError
from decepticon.middleware.policy_compiler import compile_openshell_policy
from decepticon_core.types.roe import MachineEnforcement, ScopeRule

pytest.importorskip("openshell")


@pytest.fixture
def compiled_policy():
    compiled = compile_openshell_policy(
        MachineEnforcement(in_scope=(ScopeRule(pattern="example.com"),))
    )
    assert not compiled.errors
    return openshell_local._policy_spec(compiled).policy


@pytest.mark.parametrize(
    "health_wire",
    [
        b'{"status":200,"body":{"status":"ok","pid":42,"start":"fixture"}}',
        b"not-json",
        b'{"status":401,"body":{}}',
    ],
)
def test_real_sdk_serializes_daemon_and_poll_timeouts_before_fixture_exec(health_wire):
    import grpc
    from openshell import SandboxClient
    from openshell._proto import openshell_pb2 as pb
    from openshell._proto import openshell_pb2_grpc
    from openshell.sandbox import SandboxSession

    requests = []

    class Gateway(openshell_pb2_grpc.OpenShellServicer):
        def ExecSandbox(self, request, context):
            requests.append(request)
            # Deliberately do not execute the supplied command.
            if request.command[-1] == MANAGED_START:
                yield pb.ExecSandboxEvent(
                    stdout=pb.ExecSandboxStdout(data=b'{"pid":42,"start":"fixture","reused":false}')
                )
            if request.command[-1] == MANAGED_BRIDGE:
                yield pb.ExecSandboxEvent(stdout=pb.ExecSandboxStdout(data=health_wire))
            yield pb.ExecSandboxEvent(exit=pb.ExecSandboxExit(exit_code=0))

    with ThreadPoolExecutor(max_workers=1) as executor:
        server = grpc.server(executor)
        openshell_pb2_grpc.add_OpenShellServicer_to_server(Gateway(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        assert port
        server.start()
        client = SandboxClient(f"127.0.0.1:{port}")
        try:
            session = SandboxSession(
                client, SimpleNamespace(name="owned", workspace="fixture", id="fixture-only")
            )
            if health_wire.startswith(b'{"status":200'):
                openshell_local._wait_for_session_daemon(
                    session, approved_roe=b'{"machine_enforcement":{}}'
                )
            else:
                with pytest.raises(SandboxError, match="readiness"):
                    openshell_local._wait_for_session_daemon(
                        session, approved_roe=b'{"machine_enforcement":{}}'
                    )
        finally:
            client.close()
            server.stop(0).wait()
    assert len(requests) == 2
    assert all(request.execution_timeout.seconds == 15 for request in requests)
    assert all(request.no_login_shell for request in requests)
    assert all(request.workspace_scope.workspace == "fixture" for request in requests)
    assert requests[0].command[:2] == ["/usr/bin/python3", "-I"]
    assert requests[0].command[-1] == MANAGED_START
    assert requests[1].command[0] == "/usr/bin/python3"


def test_selected_proto_retains_l7_protocol_vocabulary(compiled_policy):
    from google.protobuf.descriptor import FieldDescriptor

    endpoint = compiled_policy.network_policies["roe_in_scope"].endpoints[0]
    field = endpoint.DESCRIPTOR.fields_by_name["protocol"]
    assert field.type == FieldDescriptor.TYPE_STRING
    assert field.enum_type is None
    assert endpoint.protocol == "rest"
    decoded = type(compiled_policy).FromString(compiled_policy.SerializeToString())
    assert decoded.network_policies["roe_in_scope"].endpoints[0].protocol == "rest"


@pytest.mark.parametrize(
    "mode,path",
    [
        ("read_write", "/"),
        ("read_write", "/root"),
        ("read_write", "/etc"),
        ("read_write", "/proc"),
        ("read_write", "/tmp/../root"),
        ("read_only", "/root"),
        ("read_only", "/workspace/../root"),
        ("read_only", "/opt/private"),
    ],
)
def test_gateway_cannot_add_arbitrary_filesystem_authority(compiled_policy, mode, path):
    effective = type(compiled_policy)()
    effective.CopyFrom(compiled_policy)
    getattr(effective.filesystem, mode).append(path)
    with pytest.raises(SandboxError, match="effective policy"):
        openshell_local._assert_effective_policy_matches(effective, compiled_policy)


def test_selected_supervisor_baseline_is_permitted_without_dropping_compiled_grants(
    compiled_policy,
):
    effective = type(compiled_policy)()
    effective.CopyFrom(compiled_policy)
    effective.filesystem.read_only.extend(
        ["/usr", "/lib", "/etc", "/app", "/var/log", "/proc", "/dev/urandom"]
    )
    effective.filesystem.read_write.extend(["/tmp", "/dev/null"])
    openshell_local._assert_effective_policy_matches(effective, compiled_policy)


def test_runtime_baseline_cannot_replace_authored_grant(compiled_policy):
    effective = type(compiled_policy)()
    effective.CopyFrom(compiled_policy)
    effective.filesystem.ClearField("read_write")
    effective.filesystem.read_write.append("/tmp")
    with pytest.raises(SandboxError, match="effective policy"):
        openshell_local._assert_effective_policy_matches(effective, compiled_policy)


def test_non_filesystem_policy_fields_are_not_ignored(compiled_policy):
    effective = type(compiled_policy)()
    effective.CopyFrom(compiled_policy)
    effective.version += 1
    with pytest.raises(SandboxError, match="effective policy"):
        openshell_local._assert_effective_policy_matches(effective, compiled_policy)


def test_unknown_future_policy_field_is_not_silently_accepted(compiled_policy):
    # Unknown varint field999 must survive protobuf normalization/comparison.
    effective = type(compiled_policy).FromString(
        compiled_policy.SerializeToString() + b"\xf8\x3e\x01"
    )
    with pytest.raises(SandboxError, match="effective policy"):
        openshell_local._assert_effective_policy_matches(effective, compiled_policy)


def test_unauthorized_include_workdir_widening_is_not_ignored(compiled_policy):
    compiled_policy.filesystem.include_workdir = False
    effective = type(compiled_policy)()
    effective.CopyFrom(compiled_policy)
    effective.filesystem.include_workdir = True
    with pytest.raises(SandboxError, match="effective policy"):
        openshell_local._assert_effective_policy_matches(effective, compiled_policy)
