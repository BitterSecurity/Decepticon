"""HTTP-transport sandbox backend.

`HTTPSandbox` is a `BaseSandbox` subclass that forwards every operation
the agent needs — `execute`, `upload_files`, `download_files`,
`execute_tmux`, `start_background`, `poll_completion`, `kill_session`,
`read_session_log_diff`, `reset_session_log_offset`, `session_log_path` —
to a remote sandbox daemon over HTTP. The peer is
`decepticon.sandbox_server`, a FastAPI app that runs *inside* the
sandbox container and wraps a `LocalSandbox` (a `SandboxBase`
subclass with `docker exec` swapped for direct subprocess execution).

Architecture
------------
The existing `DockerSandbox` shells `docker exec <container> ...` into a
sibling sandbox container — great in dev where a docker daemon is
available, untenable on serverless runtimes (Cloud Run, Fargate, etc.)
that explicitly do not expose a host docker socket between sibling
containers. `HTTPSandbox` keeps **every** semantic of the existing
DockerSandbox + middleware stack — tmux session management, PS1 marker
parsing, size watchdog, auto-background after 60s, session log diff —
and only swaps the wire layer from `docker exec` to HTTP. The daemon
side re-uses the exact same `TmuxSessionManager` class via the new
`exec_prefix=[]` switch, so the agent gets bit-for-bit equivalent
behaviour from the bash tool whether it's pointed at DockerSandbox
(dev / GCE Spot) or HTTPSandbox (Cloud Run multi-container).

Why HTTP and not SSH / gRPC: OpenHands explicitly deprecated SSH
(Issue #2404) because sshd-in-every-image is untenable when users
bring their own sandbox images. E2B, Modal, Daytona, SmolAgents,
SWE-ReX all converged on REST. Cloud Run sibling-container loopback
is HTTP/1.1-first; gRPC needs h2c; SSH needs key distribution + sshd.
REST + SSE has the strongest container-runtime tool support and the
lowest configuration burden.

Class-level state
-----------------
`SandboxNotificationMiddleware` polls `sandbox._jobs.all_jobs()` to
discover which background commands are still running and then calls
`sandbox.poll_completion(...)` to refresh each one. To stay drop-in
compatible with that middleware, `HTTPSandbox` exposes a class-level
`BackgroundJobTracker` populated locally by `start_background` and
refreshed by `poll_completion`. The tracker is a *mirror* — the source
of truth still lives in the daemon's `LocalSandbox._jobs` — but
the middleware doesn't need to know which side owns the registry.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from typing import TYPE_CHECKING

import httpx
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from decepticon.sandbox_kernel import BackgroundJob, BackgroundJobTracker

if TYPE_CHECKING:
    from decepticon.sandbox_kernel.egress import EgressPolicy

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


def _retry_on_connection_error(fn, max_total_delay=150.0, base_delay=0.5, max_delay=8.0):
    """Retry a function on httpx connection errors with capped exponential backoff.

    Only ``ConnectError`` / ``ConnectTimeout`` are retried — both mean the TCP
    connection was never established, so the request never reached the daemon
    and re-sending it is side-effect-free (unlike a ``ReadTimeout``, where the
    command may already be running). That safety is what lets us retry even
    ``execute`` (bash) here.

    The retry budget is time-based (``max_total_delay`` seconds of cumulative
    sleep) rather than a fixed attempt count, because a per-engagement sandbox
    can take 1-2 minutes to become reachable: VM provision -> COS boot ->
    sandbox image pull -> container start before the daemon listens on :9999.
    During that window connections are refused; retrying for ~``max_total_delay``
    lets the agent's first tool call ride out the boot instead of failing the
    run. A genuinely dead/unroutable sandbox still surfaces the error within
    roughly ``max_total_delay`` (delays are capped at ``max_delay`` so the loop
    keeps probing rather than backing off unboundedly).
    """
    last_exc = None
    slept = 0.0
    attempt = 0
    while True:
        try:
            return fn()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            last_exc = exc
            delay = min(base_delay * (2**attempt), max_delay)
            if slept + delay > max_total_delay:
                break
            log.warning(
                "Sandbox connection failed (attempt %d, %.1fs/%.0fs elapsed), "
                "retrying in %.1fs: %s",
                attempt + 1,
                slept,
                max_total_delay,
                delay,
                exc,
            )
            time.sleep(delay)
            slept += delay
            attempt += 1
    if last_exc is None:  # max_total_delay <= 0 — never able to retry
        raise SandboxError("retry loop made no attempts (max_total_delay <= 0)")
    raise last_exc


class HTTPSandbox(BaseSandbox):
    """A `BaseSandbox` that talks to a `decepticon.sandbox_server` daemon.

    The peer endpoint is a FastAPI service running inside the sandbox
    container, typically reachable over Cloud Run's loopback interface
    (`http://localhost:9999` by default) or any service-mesh address
    when deployed on Kubernetes / Fargate / ECS / etc.

    Args:
        base_url: Daemon base URL, e.g. ``http://localhost:9999``. No
            trailing slash required.
        token: Optional shared-secret bearer token. When set, the daemon
            requires every request to carry ``Authorization: Bearer
            <token>``. The Cloud Run loopback path is not network-
            reachable from outside the service, so this is defence-in-
            depth rather than a primary authn mechanism — but it is
            still strongly recommended.
        timeout: Default request timeout in seconds. Per-call timeouts
            on ``execute()`` / ``execute_tmux()`` override this for
            long-running commands.
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client: httpx.Client | None = None
        # Instance-level job tracker — mirrors daemon-side state for
        # SandboxNotificationMiddleware. Previously a ClassVar, now
        # per-instance so multiple sandbox instances don't collide.
        self._jobs = BackgroundJobTracker()

    @property
    def id(self) -> str:
        return f"http-sandbox:{self._base_url}"

    # ── httpx client lifecycle ─────────────────────────────────────────
    def _http(self) -> httpx.Client:
        """Lazily create a connection-pooled HTTP client.

        Sharing one `httpx.Client` across requests is the supported
        pattern — it keeps the TCP connection pool warm so the per-call
        overhead on a healthy loopback path stays in the microseconds
        rather than reopening a socket per request.
        """
        if self._client is None:
            headers = {"User-Agent": "decepticon-http-sandbox/1"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            self._client = httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    def close(self) -> None:
        """Best-effort cleanup. Safe to call multiple times."""
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send an HTTP request with retry on transient errors and domain error wrapping."""
        resp = _retry_on_connection_error(lambda: getattr(self._http(), method)(path, **kwargs))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SandboxError(
                f"Sandbox returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        return resp

    # ── BaseSandbox abstract methods ───────────────────────────────────
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

    def provision_egress(self, policy: "EgressPolicy") -> dict:
        """Ship a compiled RoE egress policy to the sandbox's network edge.

        The sandbox folds in its locally-discovered management plane and
        loads the nftables connect-allowlist by default for an enforcing
        policy (the operator can opt out sandbox-side via
        ``DECEPTICON_EGRESS_DISABLE``). The returned dict reports whether
        the ruleset was applied. See ``decepticon.middleware.egress`` for
        the policy compiler.
        """
        response = self._request(
            "post",
            "/provision_egress",
            json={"policy": policy.to_dict()},
        )
        return response.json()
