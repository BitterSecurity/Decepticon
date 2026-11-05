"""Daemon-routed sandbox operations — the transport-agnostic layer.

Every Decepticon sandbox transport (plain HTTP to the sandbox daemon, an
admitted OpenShell session bridge, future drivers) reaches the SAME peer:
the ``decepticon.sandbox_server`` daemon running inside the sandbox
container, over its HTTP API routes. This module implements every
agent-facing operation against those routes in terms of one abstract seam:

    ``_request(method, path, **kwargs)`` — subclasses provide the transport.

Nothing here opens sockets or knows how the route is reached; the tmux
session semantics themselves (PS1 markers, size watchdog, auto-background,
``TmuxSessionManager``) always lived on the daemon side
(``decepticon.sandbox_kernel``), so this layer is purely route + local
background-job bookkeeping. Adding a transport means subclassing and
implementing ``_request`` — see ``HTTPTransportSandbox`` (plain HTTP) and
``OpenShellSandbox`` (admitted session bridge).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from decepticon.sandbox_kernel import BackgroundJob, BackgroundJobTracker

log = logging.getLogger(__name__)

_WORKSPACE_DEFAULT = "/workspace"


def _normalize_workspace(workspace_path: str | None) -> str:
    path = (workspace_path or _WORKSPACE_DEFAULT).strip()
    if path == _WORKSPACE_DEFAULT:
        return path
    if not path.startswith("/workspace/"):
        return _WORKSPACE_DEFAULT
    path = path.rstrip("/")
    components = path[len("/workspace/") :].split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", c) for c in components):
        return _WORKSPACE_DEFAULT
    return path


def _workspace_slug(workspace_path: str) -> str:
    path = _normalize_workspace(workspace_path)
    if path == _WORKSPACE_DEFAULT:
        return "root"
    digest = hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    slug = path.rsplit("/", 1)[-1] or "workspace"
    safe_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", slug).strip("-") or "workspace"
    return f"{safe_slug}-{digest}"


def _mirror_key(session: str, workspace_path: str | None) -> str:
    path = _normalize_workspace(workspace_path)
    if path == _WORKSPACE_DEFAULT:
        return session
    return f"{_workspace_slug(path)}:{session}"


class SandboxError(RuntimeError):
    """Domain error for sandbox HTTP failures."""

    pass


class SandboxDaemonClient(BaseSandbox):
    """Agent operations implemented over the sandbox daemon's HTTP API.

    Subclasses implement exactly one seam — ``_request(method, path,
    **kwargs)`` — returning an object with ``.json()``. Everything else in
    this class is transport-agnostic: the tmux/session routes, the local
    background-job mirror (``BackgroundJobTracker``), and the file transfer
    routes.
    """

    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout
        # Instance-level job tracker — mirrors daemon-side state for
        # SandboxNotificationMiddleware. Previously a ClassVar, now
        # per-instance so multiple sandbox instances don't collide.
        self._jobs = BackgroundJobTracker()

    def _request(self, method: str, path: str, **kwargs):
        raise NotImplementedError("SandboxDaemonClient subclasses must implement _request()")

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        # The per-call request timeout is bumped above `timeout` so the
        # remote has a moment to finish + return after its own command
        # timeout fires. Without this margin, httpx would abort just as
        # the remote was sending the truncated-but-valid response.
        request_timeout = (timeout + 10) if timeout is not None else None
        response = self._request(
            "post",
            "/execute",
            json={"command": command, "timeout": timeout},
            timeout=request_timeout if request_timeout is not None else self._timeout,
        )
        data = response.json()

        return ExecuteResponse(
            output=data["output"],
            exit_code=data.get("exit_code"),
            truncated=data.get("truncated", False),
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        payload = {
            "files": [
                {
                    "path": path,
                    "data_b64": base64.b64encode(data).decode("ascii"),
                }
                for path, data in files
            ]
        }
        response = self._request("post", "/upload_files", json=payload)
        data = response.json()

        return [
            FileUploadResponse(path=item["path"], error=item.get("error")) for item in data["files"]
        ]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        response = self._request("post", "/download_files", json={"paths": paths})
        data = response.json()

        out: list[FileDownloadResponse] = []
        for item in data["files"]:
            content_b64 = item.get("data_b64")
            content = base64.b64decode(content_b64) if content_b64 is not None else None
            out.append(
                FileDownloadResponse(
                    path=item["path"],
                    content=content,
                    error=item.get("error"),
                )
            )
        return out

    # ── tmux / background surface — mirrors DockerSandbox ────────────────
    # These are the methods the bash tool consumes via
    # `decepticon/tools/bash/bash.py:_sandbox.execute_tmux_async(...)`
    # etc. The HTTP transport adds <1ms of overhead on loopback compared
    # to docker-exec; the tmux session state itself lives on the daemon
    # side where TmuxSessionManager always lived.

    def execute_tmux(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str:
        """Run `command` in the named tmux session and return the output.

        Mirrors `DockerSandbox.execute_tmux`. See that class for the full
        protocol contract (PS1 markers, size watchdog, auto-background
        after 60 s, output truncation at 30 K chars).
        """
        request_timeout = (timeout + 10) if timeout is not None else None
        response = self._request(
            "post",
            "/execute_tmux",
            json={
                "command": command,
                "session": session,
                "timeout": timeout,
                "is_input": is_input,
                "workspace_path": workspace_path,
            },
            timeout=request_timeout if request_timeout is not None else self._timeout,
        )
        return response.json()["output"]

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
        on_auto_background=None,
    ) -> str:
        """Async wrapper around `execute_tmux`.

        DockerSandbox.execute_tmux_async is cancellable via
        `asyncio.CancelledError` — when the supervisor's task() call gets
        cancelled mid-run, the bash command is auto-backgrounded. We
        approximate that here by running the sync call in a thread so
        cancellation cleanly propagates through `httpx`'s timeout
        machinery. The remote daemon's own auto-background timer
        (`AUTO_BACKGROUND_SECONDS = 60 s`) still fires on its side, so
        long-running commands are tracked as background jobs even if the
        client disconnects.

        `on_auto_background` is accepted for signature parity with
        DockerSandbox but currently isn't invoked — the daemon already
        records the background-job state itself, and `poll_completion`
        on the next middleware tick surfaces it.
        """
        _ = on_auto_background  # signature parity; behaviour deferred
        return await asyncio.to_thread(
            self.execute_tmux,
            command=command,
            session=session,
            timeout=timeout,
            is_input=is_input,
            workspace_path=workspace_path,
        )

    def start_background(
        self,
        command: str,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        """Launch `command` in the background; register a local mirror job."""
        self._request(
            "post",
            "/start_background",
            json={
                "command": command,
                "session": session,
                "workspace_path": workspace_path,
            },
        )
        # The daemon owns the canonical BackgroundJob (it just stamped
        # `initial_markers` from the live tmux pane state, which we
        # don't have visibility into). Drop a provisional entry into
        # the local mirror so SandboxNotificationMiddleware's
        # iteration can find it; `poll_completion` will replace the
        # stub's status / exit_code on the next refresh tick.
        ws = _normalize_workspace(workspace_path)
        mirror_key = _mirror_key(session, ws)
        self._jobs.register(
            session=session,
            command=command,
            initial_markers=0,
            key=mirror_key,
            workspace_path=ws,
        )

    def poll_completion(
        self,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> BackgroundJob | None:
        """Return the latest BackgroundJob for `session` (None if missing)."""
        response = self._request(
            "post",
            "/poll_completion",
            json={"session": session, "workspace_path": workspace_path},
        )
        data = response.json()

        if data.get("job") is None:
            return None
        j = data["job"]
        job = BackgroundJob(
            session=j["session"],
            key=j["key"],
            command=j["command"],
            initial_markers=j["initial_markers"],
            started_at=j["started_at"],
            workspace_path=j.get("workspace_path", "/workspace"),
            status=j.get("status", "running"),
            exit_code=j.get("exit_code"),
            completed_at=j.get("completed_at"),
            consumed=j.get("consumed", False),
        )
        mirror_key = _mirror_key(job.session, job.workspace_path)
        local = self._jobs.get(session=job.session, key=mirror_key)
        if local is None:
            self._jobs.register(
                session=job.session,
                command=job.command,
                initial_markers=job.initial_markers,
                key=mirror_key,
                workspace_path=job.workspace_path,
            )
            local = self._jobs.get(session=job.session, key=mirror_key)
        if local is not None and job.status != "running":
            self._jobs.mark_complete(
                session=job.session,
                exit_code=job.exit_code if job.exit_code is not None else -1,
                key=mirror_key,
            )
        return job

    def kill_session(
        self,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        self._request(
            "post",
            "/kill_session",
            json={"session": session, "workspace_path": workspace_path},
        )
        # Clear the agent-side mirror job. The daemon already dropped its own
        # tracker entry inside the /kill_session route (base.kill_session), but
        # bash_status / pending_completions read THIS local mirror — without
        # this remove a killed session lingers as "running" until a later
        # poll_completion reconciles it. Keeps the two transports' local state
        # in parity (idempotent: remove() is a no-op when absent).
        self._jobs.remove(session, key=_mirror_key(session, workspace_path))

    def read_session_log_diff(
        self,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> str:
        response = self._request(
            "post",
            "/read_session_log_diff",
            json={"session": session, "workspace_path": workspace_path},
        )
        return response.json()["diff"]

    def reset_session_log_offset(
        self,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        self._request(
            "post",
            "/reset_session_log_offset",
            json={"session": session, "workspace_path": workspace_path},
        )

    def session_log_path(
        self,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> str:
        response = self._request(
            "post",
            "/session_log_path",
            json={"session": session, "workspace_path": workspace_path},
        )
        return response.json()["path"]
