"""HTTP-transport sandbox backend — the plain-HTTP wire implementation.

`HTTPSandbox` is a `SandboxDaemonClient` subclass whose transport is a
plain-HTTP client to the `decepticon_sandbox.server` daemon running inside
the sandbox container. It carries no routing logic of its own: every
agent-facing operation (execute, tmux, file transfer, background jobs) is
inherited from `SandboxDaemonClient`; this class provides only the wire —
a pooled `httpx.Client`, transient-error retry, and status-code →
`SandboxError` translation.

Architecture note: this class used to be the whole sandbox client (routes
+ wire in one). The daemon-routed operations now live in
`decepticon.backends.daemon_client` so alternative transports (the
admitted OpenShell session bridge, future drivers) can share them without
inheriting HTTP baggage. The OpenShell peer is `OpenShellSandbox`.

Deployment note: the HTTP transport exists for NON-engagement contexts —
tests, benchmarks, and pre-scope file work. The OpenShell provider is the
default; under a materialized RoE the RoE guard refuses gated tools on
this transport (there is no fallback that quietly runs engagement
workloads without the kernel boundary).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from deepagents.backends.protocol import (  # noqa: F401 - back-compat re-exports
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)

from decepticon.backends.daemon_client import (  # noqa: F401 - back-compat re-exports
    SandboxDaemonClient,
    SandboxError,
    _mirror_key,
    _normalize_workspace,
    _workspace_slug,
)

log = logging.getLogger(__name__)


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


class HTTPTransportSandbox(SandboxDaemonClient):
    """The plain-HTTP wire transport to the sandbox daemon.

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
        super().__init__(timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: httpx.Client | None = None

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

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an HTTP request with retry on transient errors and domain error wrapping."""
        resp = _retry_on_connection_error(lambda: getattr(self._http(), method)(path, **kwargs))
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SandboxError(
                f"Sandbox returned {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        return resp


# Back-compat alias — the transport identity is now explicit in the name.
HTTPSandbox = HTTPTransportSandbox
