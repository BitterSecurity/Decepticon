"""Shared sandbox kernel — utilities used by the agent-side HTTP client
and the in-container HTTP daemon.

Layering: this package is the lowest layer of the sandbox stack. It
contains the tmux session manager, the background-job tracker, and the
``DaemonSandbox`` class that the daemon wraps. Two consumers hang off it:

  ``decepticon.backends.http_transport.HTTPTransportSandbox`` — agent-side,
      uses HTTP as transport. Imports ``BackgroundJob`` and
      ``BackgroundJobTracker`` from this package so the
      ``SandboxNotificationMiddleware`` mirror pattern works against
      the remote daemon's state.

  ``decepticon.sandbox_server.app`` — sandbox-side, FastAPI daemon.
      Imports ``DaemonSandbox`` from this package and exposes its
      methods over HTTP. Instantiates ``DaemonSandbox(exec_prefix=[])``
      so tmux is driven by local subprocesses inside the container.

Historical note: an earlier ``DockerSandbox`` ran the same machinery
from the agent host via ``exec_prefix=["docker", "exec", <ctn>]``. That
transport was retired in favor of ``HTTPTransportSandbox`` + the in-container
daemon (see ``backends/factory.py`` — "there is no longer a docker-exec
transport"). ``TmuxSessionManager`` still accepts a non-empty
``exec_prefix`` for parity with the old wire format, but no production
caller exercises that branch.

Why a separate package: the OSS sandbox container image is a *passive*
container (Kali Linux + red-team tools + tmux); historically it shipped
zero decepticon Python code. Adding the daemon required *some* in-
container Python, but the agent-side transport (``HTTPTransportSandbox``,
``factory``) has no business inside the sandbox. Splitting the shared
utilities out keeps the original "agent has everything, sandbox has
nothing" boundary intact: the sandbox image ships only
``sandbox_kernel`` + ``sandbox_server``; the agent (langgraph image)
keeps ``backends`` + everything else.
"""

from decepticon.sandbox_kernel.jobs import BackgroundJob, BackgroundJobTracker
from decepticon.sandbox_kernel.tmux import (
    AUTO_BACKGROUND_SECONDS,
    MAX_OUTPUT_CHARS,
    POLL_INTERVAL,
    PS1_PATTERN,
    SIZE_WATCHDOG_CHARS,
    STALL_SECONDS,
    TmuxCommandError,
    TmuxSessionManager,
)

__all__ = [
    "AUTO_BACKGROUND_SECONDS",
    "BackgroundJob",
    "BackgroundJobTracker",
    "MAX_OUTPUT_CHARS",
    "POLL_INTERVAL",
    "PS1_PATTERN",
    "SIZE_WATCHDOG_CHARS",
    "STALL_SECONDS",
    "TmuxCommandError",
    "TmuxSessionManager",
]
