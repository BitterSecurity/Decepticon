"""Local trusted OpenShell admission resolver for self-hosted OSS flows.

In a self-hosted OSS deployment the operator *is* the trusted host. Setting
``DECEPTICON_SANDBOX_PROVIDER=openshell`` plus the explicit
``DECEPTICON_OPENSHELL_LOCAL=1`` opt-in installs this resolver, which:

1. reads the engagement's ``plan/roe.json`` (never agent-supplied input),
2. compiles it into an OpenShell sandbox policy
   (:mod:`decepticon.middleware.policy_compiler`) — a compile error refuses
   the binding, so an engagement never runs without its kernel boundary,
3. creates (or reuses) an engagement-scoped sandbox with that policy
   attached via the OpenShell SDK ``SandboxSpec.policy`` — the sandbox is
   born confined, never granted-then-narrowed,
4. starts the retained session daemon through the admitted exec path (the
   image ENTRYPOINT is not a daemon lifecycle contract under a managed
   runtime), and
5. returns an :class:`~decepticon.backends.openshell_sandbox.OpenShellSandbox`
   whose admission binds ``effective_policy_hash`` to the compiled RoE
   policy hash and ``approval_hash`` to the RoE file bytes.

The sandbox locator hashes the full canonical workspace, compiled policy and
approved document. The complete binding digest is checked through gateway
metadata before and after policy/ACK reads; a short-name collision never grants
access. A changed approved document gets a fresh binding. One backend is cached
per complete binding so agents in the same engagement share one job view.

Without ``DECEPTICON_OPENSHELL_LOCAL=1`` nothing is installed and the
factory keeps demanding an operator-provided resolver — the env flag is
the operator's trust decision, never something agent config can set.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from decepticon.backends.http_transport import SandboxError
from decepticon.middleware.policy_compiler import (
    CompiledRoePolicy,
    compile_openshell_policy,
    sandbox_binding_digest,
    sandbox_name,
)

log = logging.getLogger(__name__)

_ADMISSION_TTL_SECONDS = 3600.0
_READY_TIMEOUT_SECONDS = 120.0
_DAEMON_START_TIMEOUT_SECONDS = 15
_DAEMON_READY_TIMEOUT_SECONDS = 30.0
_BRIDGE_POLL_SECONDS = 0.5
_BINDING_LABEL = "decepticon-binding"

# OpenShell v0.1.6 supervisor, ac7f615cd12d5cc49cb16eb1f5f10ffeb1e90f47,
# crates/openshell-supervisor/src/lib.rs, PROXY_BASELINE_{READ_ONLY,READ_WRITE}.
# Permit only these runtime additions, not an arbitrary filesystem superset.
_PROXY_BASELINE_READ_ONLY = frozenset(
    ("/usr", "/lib", "/etc", "/app", "/var/log", "/proc", "/dev/urandom")
)
_PROXY_BASELINE_READ_WRITE = frozenset(("/tmp", "/dev/null"))

# The sandbox image is the same artifact the compose daemon deployment
# ships; the managed runtime ignores its ENTRYPOINT lifecycle, so the
# daemon is started explicitly below exactly like the reference runtime
# test does.
_DEFAULT_OPENSHELL_IMAGE = "ghcr.io/bittersecurity/decepticon-sandbox:latest"

_DAEMON_START_COMMAND = (
    "mkdir -p /workspace; "
    "PYTHONPATH=/opt DECEPTICON_SKIP_BOOT=1 SANDBOX_ROOT_DIR=/workspace "
    "SANDBOX_DAEMON_HOST=127.0.0.1 "
    "/usr/bin/python3 -m decepticon.sandbox_server >/tmp/session-daemon.log 2>&1 </dev/null & "
    "sleep 1; cat /tmp/session-daemon.log"
)

_bridge_command = [
    "/usr/bin/python3",
    "/opt/decepticon/sandbox_server/session_client.py",
]

_backends: dict[tuple[str, str, str], Any] = {}


def local_resolver_enabled() -> bool:
    """Trusted local admission resolver — ON by default, explicit opt-out only.

    The default provider is openshell (#1144: OpenShell is the only
    network-boundary authority), so the trusted host resolver must come up
    with the daemon. The env var now only DISABLES it (for exotic
    deployments that wire their own resolver through
    ``configure_openshell_resolver``); agent config still can never install
    one — the factory guard is unchanged.
    """
    return os.environ.get("DECEPTICON_OPENSHELL_LOCAL", "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _openshell_workspace() -> str:
    return os.environ.get("DECEPTICON_OPENSHELL_WORKSPACE", "").strip() or "default"


def _openshell_image() -> str:
    return os.environ.get("DECEPTICON_OPENSHELL_IMAGE", "").strip() or _DEFAULT_OPENSHELL_IMAGE


def _client():
    from openshell import SandboxClient

    endpoint = os.environ.get("DECEPTICON_OPENSHELL_ENDPOINT", "").strip()
    if endpoint:
        return SandboxClient(endpoint, timeout=30.0)
    return SandboxClient.from_active_cluster(timeout=30.0)


def _approval_hash(workspace_path: str) -> str:
    roe_path = Path(workspace_path) / "plan" / "roe.json"
    return hashlib.sha256(roe_path.read_bytes()).hexdigest()


def _policy_spec(compiled: CompiledRoePolicy):
    """Convert the compiled mapping into a ``SandboxSpec`` with policy attached.

    The YAML vocabulary maps onto the proto: ``filesystem_policy`` →
    ``filesystem``, and the rule strings are the enum symbol names. This is
    the same translation the gateway CLI applies to ``--policy`` YAML;
    verified against the ``openshell`` proto this module ships with.
    """
    from google.protobuf.json_format import ParseDict
    from openshell._proto import openshell_pb2

    policy = dict(compiled.policy)
    policy["filesystem"] = policy.pop("filesystem_policy")
    policy = _apply_enum_symbols(policy)
    spec = openshell_pb2.SandboxSpec()
    spec.template.image = _openshell_image()
    ParseDict(policy, spec.policy)
    if not spec.policy.version:
        raise RuntimeError("compiled OpenShell policy failed proto conversion")
    return spec


def _apply_enum_symbols(node: Any) -> Any:
    """Rewrite YAML enum symbols into proto enum value names, recursively."""
    symbols = {
        "enforcement": {
            "enforce": "NETWORK_ENFORCEMENT_MODE_ENFORCE",
            "audit": "NETWORK_ENFORCEMENT_MODE_AUDIT",
        },
        "access": {
            "full": "NETWORK_ACCESS_PRESET_FULL",
            "read-write": "NETWORK_ACCESS_PRESET_READ_WRITE",
            "read-only": "NETWORK_ACCESS_PRESET_READ_ONLY",
        },
    }
    if isinstance(node, dict):
        return {
            key: symbols.get(key, {}).get(value, _apply_enum_symbols(value))
            if isinstance(value, str)
            else _apply_enum_symbols(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_apply_enum_symbols(item) for item in node]
    return node


def _generation_token(ref: Any) -> str:
    """Runtime identity: any policy reload or process replacement invalidates."""
    status = ref.status
    return (
        f"{ref.current_policy_version}:{status.main_process_started_at_ms or 0}:"
        f"{status.restart_count}"
    )


def _assert_effective_policy_matches(effective, compiled_spec):
    """Compare the full protobuf except selected supervisor baseline additions.

    The supervisor adds existing baseline paths without dropping authored
    grants. Arbitrary extra paths, permission upgrades, changed flags and
    unrelated/unknown protobuf content are not runtime normalization.
    """
    if effective.network_policies != compiled_spec.network_policies:
        raise SandboxError("OpenShell effective policy does not match compiled RoE")
    if getattr(effective, "process", None) != getattr(compiled_spec, "process", None):
        raise SandboxError("OpenShell effective policy process block does not match compiled RoE")
    if getattr(effective, "landlock", None) != getattr(compiled_spec, "landlock", None):
        raise SandboxError("OpenShell effective policy landlock block does not match compiled RoE")
    lfs, efs = compiled_spec.filesystem, effective.filesystem
    if lfs.include_workdir and not efs.include_workdir:
        raise SandboxError("OpenShell effective policy dropped include_workdir")
    if not set(lfs.read_only) <= set(efs.read_only):
        raise SandboxError("OpenShell effective policy dropped operator read-only grants")
    if not set(lfs.read_write) <= set(efs.read_write):
        raise SandboxError("OpenShell effective policy dropped operator read-write grants")
    if not set(efs.read_only) - set(lfs.read_only) <= _PROXY_BASELINE_READ_ONLY:
        raise SandboxError("OpenShell effective policy added unauthorized read-only grants")
    if not set(efs.read_write) - set(lfs.read_write) <= _PROXY_BASELINE_READ_WRITE:
        raise SandboxError("OpenShell effective policy added unauthorized read-write grants")
    normalized = type(effective)()
    normalized.CopyFrom(effective)
    normalized.filesystem.read_only[:] = lfs.read_only
    normalized.filesystem.read_write[:] = lfs.read_write
    if normalized != compiled_spec:
        raise SandboxError("OpenShell effective policy does not match compiled RoE")


def _verified_runtime(
    client: Any, name: str, workspace: str, compiled: CompiledRoePolicy, *, binding_digest: str
):
    """Read effective policy and loaded ACK, bracketing them with runtime identity.

    Expected compiler output is never an observation. Both new and reused
    sandboxes must prove the complete effective protobuf equals that output.
    The SDK has no public policy-read wrapper; use its authenticated stub so
    TLS/bearer configuration and bounded RPC deadlines are retained.
    """
    from openshell._proto import datamodel_pb2, openshell_pb2, sandbox_pb2

    before = client.get(name, workspace=workspace)
    if before.status.phase != openshell_pb2.SANDBOX_PHASE_READY:
        raise SandboxError("OpenShell sandbox is not ready")
    if not binding_digest or before.labels.get(_BINDING_LABEL) != binding_digest:
        raise SandboxError("OpenShell binding does not match owned workspace and policy")
    before_generation = _generation_token(before)
    before_id = before.id
    before_version = before.current_policy_version
    selector = datamodel_pb2.WorkspaceSelector(workspace=workspace)
    config = client._stub.GetSandboxConfig(
        sandbox_pb2.GetSandboxConfigRequest(workspace_scope=selector, name=name),
        timeout=30.0,
    )
    if config.version < 1:
        raise SandboxError("OpenShell sandbox config has no admitted policy")
    _assert_effective_policy_matches(config.policy, _policy_spec(compiled).policy)
    status = client._stub.GetSandboxPolicyStatus(
        openshell_pb2.GetSandboxPolicyStatusRequest(
            workspace_scope=selector,
            sandbox=name,
            version=config.version,
        ),
        timeout=30.0,
    )
    revision = status.revision
    if not (
        revision.status == openshell_pb2.POLICY_STATUS_LOADED
        and revision.version == config.version == status.active_version
        and config.policy_hash
        and revision.policy_hash == config.policy_hash
        and revision.policy == config.policy
    ):
        raise SandboxError("OpenShell effective policy has no matching loaded acknowledgement")
    after = client.get(name, workspace=workspace)
    if not (
        after.id == before_id
        and after.status.phase == openshell_pb2.SANDBOX_PHASE_READY
        and after.current_policy_version == config.version == before_version
        and _generation_token(after) == before_generation
        and after.labels.get(_BINDING_LABEL) == binding_digest
    ):
        raise SandboxError("OpenShell runtime changed during policy verification")
    return after


def _receipt(
    ref: Any, *, binding_id: str, workspace: str, compiled: CompiledRoePolicy, approval_hash: str
):
    from decepticon.backends.openshell_sandbox import OpenShellAdmission

    return OpenShellAdmission(
        binding_id=binding_id,
        sandbox_id=ref.id,
        workspace=workspace,
        runtime_generation=_generation_token(ref),
        approval_hash=approval_hash,
        effective_policy_hash=compiled.canonical_hash,
        expires_at=time.time() + _ADMISSION_TTL_SECONDS,
        ready=True,
    )


def _wait_for_session_daemon(session: Any) -> None:
    daemon = session.exec(
        ["/bin/sh", "-c", _DAEMON_START_COMMAND],
        timeout_seconds=_DAEMON_START_TIMEOUT_SECONDS,
        no_login_shell=True,
    )
    if daemon.exit_code != 0:
        raise RuntimeError(f"OpenShell session daemon failed to start: {daemon.stderr[:400]}")
    deadline = time.monotonic() + _DAEMON_READY_TIMEOUT_SECONDS
    while True:
        bridge = session.exec(
            _bridge_command,
            stdin=json.dumps({"path": "/poll_completion", "body": {"session": "none"}}).encode(),
            timeout_seconds=_DAEMON_START_TIMEOUT_SECONDS,
            no_login_shell=True,
        )
        if bridge.exit_code == 0:
            return
        if time.monotonic() >= deadline:
            logs = session.exec(["/bin/cat", "/tmp/session-daemon.log"], no_login_shell=True)
            raise RuntimeError(
                "OpenShell session daemon did not become ready: "
                f"{(logs.stdout + logs.stderr)[:400]}"
            )
        time.sleep(_BRIDGE_POLL_SECONDS)


def _attach_or_create(
    client: Any, name: str, workspace: str, compiled: CompiledRoePolicy, *, binding_digest: str
) -> Any:
    try:
        client.get(name, workspace=workspace)
        return _verified_runtime(client, name, workspace, compiled, binding_digest=binding_digest)
    except Exception as exc:  # noqa: BLE001 - get() raises gateway errors for absent sandboxes
        if "not found" not in str(exc).lower() and "NOT_FOUND" not in str(exc):
            raise
    client.create(
        workspace=workspace,
        name=name,
        spec=_policy_spec(compiled),
        labels={_BINDING_LABEL: binding_digest},
    )
    ready = client.wait_ready(name, workspace=workspace, timeout_seconds=_READY_TIMEOUT_SECONDS)
    if ready.current_policy_version < 1:
        raise RuntimeError("OpenShell sandbox became ready without a loaded policy")
    return _verified_runtime(client, name, workspace, compiled, binding_digest=binding_digest)


def resolve_local_binding(run_config: Any):
    """Trusted-host resolver: RoE → compiled policy → born-confined sandbox."""
    from decepticon.backends.openshell_sandbox import (  # noqa: F401 - re-exported in closures
        OpenShellAdmission,
        OpenShellSandbox,
    )
    from decepticon.middleware.roe import _load_rules_for_workspace

    try:
        from openshell import SandboxClient  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "OpenShell provider requires the 'openshell' package "
            "(pip install openshell) for the local admission resolver"
        ) from exc

    config = run_config if isinstance(run_config, dict) else {}
    configurable = config.get("configurable", {})
    configurable = configurable if isinstance(configurable, dict) else {}
    workspace_path = configurable.get("workspace_path")
    if not isinstance(workspace_path, str) or not workspace_path:
        raise RuntimeError(
            "OpenShell binding requires the run's workspace_path; no engagement context"
        )
    workspace_path = str(Path(workspace_path).resolve())

    cache_key: tuple[str, str, str] | None = None
    cached = None
    if not (Path(workspace_path) / "plan" / "roe.json").is_file():
        raise RuntimeError("OpenShell binding requires a materialized plan/roe.json")
    initial_approval_hash = _approval_hash(workspace_path)
    rules = _load_rules_for_workspace(workspace_path)
    if not rules.configured:
        raise RuntimeError(
            f"OpenShell binding for {workspace_path} requires a materialized plan/roe.json"
        )
    compiled = compile_openshell_policy(rules)
    if compiled.errors:
        raise RuntimeError(
            "RoE cannot be compiled into an OpenShell policy: " + "; ".join(compiled.errors)
        )
    # The binding is one engagement scoped to ONE compiled RoE; a changed RoE
    # is a new binding (fresh sandbox name), never an in-place policy swap.
    cache_key = (workspace_path, compiled.canonical_hash, initial_approval_hash)
    cached = _backends.get(cache_key)
    if cached is not None:
        try:
            cached.check_admission()
            return cached
        except SandboxError:
            _backends.pop(cache_key, None)  # stale receipt: re-resolve below

    workspace = _openshell_workspace()
    approval_hash = _approval_hash(workspace_path)
    if approval_hash != initial_approval_hash:
        raise SandboxError("OpenShell RoE changed while preparing admission")
    name = sandbox_name(workspace_path, compiled.canonical_hash, approval_hash=approval_hash)
    binding_digest = sandbox_binding_digest(
        workspace_path, compiled.canonical_hash, approval_hash=approval_hash
    )
    client = _client()
    ref = _attach_or_create(client, name, workspace, compiled, binding_digest=binding_digest)
    session = client.get_session(name, workspace=workspace)
    _wait_for_session_daemon(session)
    binding_id = (
        configurable.get("openshell_binding_id")
        if isinstance(configurable.get("openshell_binding_id"), str)
        and configurable.get("openshell_binding_id")
        else f"local-{name}"
    )
    receipt = _receipt(
        ref,
        binding_id=binding_id,
        workspace=workspace,
        compiled=compiled,
        approval_hash=approval_hash,
    )
    # Shared holder: renewal swaps the receipt in place so current_admission
    # keeps returning the SAME object while the runtime identity is stable
    # (receipt equality is what distinguishes "unchanged" from "revoked").
    holder: dict[str, OpenShellAdmission] = {"receipt": receipt}

    def _verified_receipt():
        try:
            if _approval_hash(workspace_path) != approval_hash:
                raise SandboxError("OpenShell approved RoE changed")
            live = _verified_runtime(
                client, name, workspace, compiled, binding_digest=binding_digest
            )
        except Exception as exc:  # noqa: BLE001 - admission must fail closed
            raise SandboxError("OpenShell admission source is unavailable") from exc
        return _receipt(
            live,
            binding_id=binding_id,
            workspace=workspace,
            compiled=compiled,
            approval_hash=approval_hash,
        )

    def current_admission():
        # While the sandbox runtime identity is unchanged the SAME receipt
        # object must come back: the sandbox compares receipts for equality,
        # and a recomputed expires_at would make every call look revoked.
        # A runtime/policy generation change returns the live receipt so the
        # mismatch fails closed on the next execution.
        try:
            if _approval_hash(workspace_path) != approval_hash:
                raise SandboxError("OpenShell approved RoE changed")
            live = _verified_runtime(
                client, name, workspace, compiled, binding_digest=binding_digest
            )
        except Exception as exc:  # noqa: BLE001 - admission must fail closed
            raise SandboxError("OpenShell admission source is unavailable") from exc
        if _generation_token(live) == holder["receipt"].runtime_generation:
            return holder["receipt"]
        fresh = _receipt(
            live,
            binding_id=binding_id,
            workspace=workspace,
            compiled=compiled,
            approval_hash=approval_hash,
        )
        holder["receipt"] = fresh
        return fresh

    def renew_admission():
        # Same trust chain as current_admission, but always rebuild the
        # receipt so an expired TTL renews without reprovisioning. The
        # approved-RoE hash check and the live-runtime verification above
        # keep this fail-closed against RoE edits and policy swaps.
        fresh = _verified_receipt()
        holder["receipt"] = fresh
        return fresh

    backend = OpenShellSandbox(
        session,
        admission=receipt,
        current_admission=current_admission,
        renew_admission=renew_admission,
    )
    _backends[cache_key] = backend
    log.info(
        "openshell: admitted %s (sandbox=%s roe_policy=%s)",
        workspace_path,
        name,
        compiled.canonical_hash[:12],
    )
    return backend


def maybe_install_local_resolver() -> bool:
    """Install the default local resolver unless the host explicitly disables it."""
    if not local_resolver_enabled():
        return False
    from decepticon.backends import factory

    if factory._openshell_resolver is None:
        factory.configure_openshell_resolver(resolve_local_binding)
    return True
