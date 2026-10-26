"""OpenShell execution transport retaining Decepticon's daemon/session contract.

The host supplies an already provisioned SDK SandboxSession and an external
admission reader. Neither agent config nor a workspace file grants authority.
No E2B SDK, local subprocess execution, or direct daemon HTTP fallback exists.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from decepticon.backends.http_sandbox import (
    HTTPSandbox,
    SandboxError,
    _mirror_key,
    _normalize_workspace,
)
from decepticon.sandbox_server.session_client import MAX_MESSAGE_BYTES, SESSION_PATHS


class OpenShellSession(Protocol):
    @property
    def id(self) -> str: ...

    def exec(self, command: Sequence[str], **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class OpenShellAdmission:
    """Host-verified receipt; generation/hash are not inferred from readiness."""

    binding_id: str
    sandbox_id: str
    workspace: str
    runtime_generation: str
    approval_hash: str
    effective_policy_hash: str
    expires_at: float
    ready: bool


class OpenShellSandbox(HTTPSandbox):
    """Reuse wire serialization/job bookkeeping, replacing the entire transport.

    current_admission must consult trusted host state, including revocation,
    policy ACK and owner/custody checks. It must not simply echo graph config.
    The host caches one backend per binding so all agents see the same jobs.
    """

    egress_authority = "openshell"

    def __init__(
        self,
        session: OpenShellSession,
        *,
        admission: OpenShellAdmission,
        current_admission: Callable[[], OpenShellAdmission],
        timeout: float = 120,
    ) -> None:
        super().__init__("http://127.0.0.1:9999", timeout=timeout)
        self._session = session
        self._admission = admission
        self._current_admission = current_admission
        self._closed = False
        self.check_admission()

    @property
    def id(self) -> str:
        return f"openshell:{self._admission.sandbox_id}:{self._admission.runtime_generation}"

    def check_admission(self) -> None:
        expected = self._admission
        if self._closed or self._current_admission() != expected:
            raise SandboxError("OpenShell execution admission changed or was revoked")
        if not all(
            (
                expected.binding_id,
                expected.workspace,
                expected.runtime_generation,
                expected.approval_hash,
                expected.effective_policy_hash,
            )
        ):
            raise SandboxError("OpenShell execution admission is incomplete")
        if (
            not expected.ready
            or not math.isfinite(expected.expires_at)
            or time.time() >= expected.expires_at
        ):
            raise SandboxError("OpenShell execution admission is not ready or has expired")
        if self._session.id != expected.sandbox_id:
            raise SandboxError("OpenShell session does not match the admitted sandbox")

    def _http(self) -> httpx.Client:
        raise SandboxError("Direct daemon HTTP is disabled for OpenShell")

    def close(self) -> None:
        self._closed = True

    def provision_egress(self, policy: Any) -> dict:
        raise SandboxError("OpenShell policy must be installed by the trusted control plane")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self.check_admission()
        if method.lower() != "post":
            raise SandboxError("Unsupported OpenShell session method")
        route = "/execute_tmux_async" if path == "/execute_tmux" else path
        if route not in SESSION_PATHS:
            raise SandboxError("Unsupported OpenShell session operation")
        timeout = kwargs.get("timeout", self._timeout)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or not 0 < timeout <= 3600
        ):
            raise SandboxError("Invalid OpenShell session timeout")
        body = kwargs.get("json", {})
        wire = json.dumps(
            {"path": route, "body": body, "timeout": timeout}, allow_nan=False
        ).encode()
        if len(wire) > MAX_MESSAGE_BYTES:
            raise SandboxError("OpenShell session request too large")
        # No automatic retry: a failed stream may already have run a mutation.
        result = self._session.exec(
            ["/usr/bin/python3", "/opt/decepticon/sandbox_server/session_client.py"],
            stdin=wire,
            timeout_seconds=math.ceil(timeout) + 10,
            no_login_shell=True,
        )
        if result.exit_code != 0:
            raise SandboxError("OpenShell session bridge failed; execution outcome may be unknown")
        if len(result.stdout.encode()) > MAX_MESSAGE_BYTES:
            raise SandboxError("OpenShell session response too large")
        try:
            envelope = json.loads(result.stdout)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("body"), dict):
                raise ValueError("invalid response envelope")
            if type(envelope.get("status")) is not int or not 200 <= envelope["status"] <= 599:
                raise ValueError("invalid response status")
            response = httpx.Response(envelope["status"], json=envelope["body"])
        except (ValueError, KeyError, TypeError) as error:
            raise SandboxError("Invalid OpenShell session response") from error
        if response.is_error:
            raise SandboxError(f"OpenShell session operation failed: HTTP {response.status_code}")
        if route == "/execute_tmux_async" and envelope["body"].get("background"):
            workspace = _normalize_workspace(body.get("workspace_path"))
            self._jobs.register(
                session=body.get("session", "main"),
                command=body.get("command", ""),
                initial_markers=0,
                workspace_path=workspace,
                key=_mirror_key(body.get("session", "main"), workspace),
            )
        return response

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
        on_auto_background: Any = None,
    ) -> str:
        # Keep the existing HTTP async callback contract; daemon job polling
        # owns completion notifications. Remote cancellation is not implied.
        _ = on_auto_background
        return await asyncio.to_thread(
            self.execute_tmux,
            command=command,
            session=session,
            timeout=timeout,
            is_input=is_input,
            workspace_path=workspace_path,
        )
