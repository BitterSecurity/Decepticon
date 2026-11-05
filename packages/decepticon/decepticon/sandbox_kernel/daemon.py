"""In-container sandbox — used by the HTTP daemon, never the agent host.

`DaemonSandbox` is what `decepticon.sandbox_server` instantiates inside
the sandbox container itself. It inherits every method of
:class:`~decepticon.sandbox_kernel.base.SandboxBase` (tmux session
management, background-job + log-offset trackers, ``execute``,
``execute_tmux``, ``start_background``, ``poll_completion``,
``kill_session``, ``read_session_log_diff``, …) but swaps the
``docker exec <container>`` command prefix for an empty list — every
subprocess call runs *directly* in the daemon process's own address
space, which is already inside the sandbox container.

File IO uses pathlib instead of ``docker cp`` for the same reason —
the daemon can't ``docker cp`` from inside the container talking to
itself.

Trust model — read this before editing!
---------------------------------------
The daemon trusts its caller and applies **no path validation**
of its own. That trust is correct *only* under the deploy invariants
below. If you ever break one of these, you must add path-prefix
validation to ``upload_files`` / ``download_files`` before shipping.

  Silo plane (per-engagement GCE Spot VM)
      Each engagement runs on its own VM. The VM mounts the GCS bucket
      with ``gcsfuse --only-dir=engagements/<org>/<eng>`` — the daemon's
      filesystem view contains only that one engagement's tree. A path
      traversal request (``/etc/shadow``, ``/workspace/other-eng/…``)
      resolves to *nothing* because the mount namespace itself has no
      other engagements visible. **Isolation = mount, not code.**

  Pool plane (Cloud Run multi-engagement)
      The daemon is NOT deployed here. Pool plane runs Soundwave +
      Decepticon orchestrator inside the langgraph container with its
      own GCS volume mount; agents talk to that mount directly through
      ``EngagementFilesystemBackend`` path virtualization (application-
      level prefix injection per engagement). No sandbox sibling, no
      daemon, no ``DaemonSandbox`` — pool plane only does file IO, the
      heavy bash tool path is reserved for silo plane.

  Dev (single-machine docker compose)
      The sandbox container's entrypoint starts the daemon
      (``SANDBOX_DAEMON=1``) so the local stack uses the same HTTP
      transport as production. The historical no-daemon path
      (``DockerSandbox`` + ``docker exec`` from the agent host) has
      been retired; ``HTTPTransportSandbox`` is the only agent-side client now.

Class-level state inheritance
-----------------------------
``_jobs``, ``_log_offsets``, ``_log_offsets_lock``,
``TmuxSessionManager._initialized``, and ``TmuxSessionManager._init_lock``
are inherited from :class:`SandboxBase` and :class:`TmuxSessionManager`
as-is. Inside the daemon process there's only ever one sandbox instance,
so the class vars are effectively instance-scoped — but keeping the
class-level contract means ``SandboxNotificationMiddleware`` (which
polls ``<Sandbox>._jobs`` directly) works unchanged when wired in front
of the daemon's backend.

Layering
--------
This module lives in ``sandbox_kernel/``, not ``backends/``, on purpose.
The sandbox container image ships only ``sandbox_kernel/`` +
``sandbox_server/`` — no agent-side transport (``DockerSandbox``,
``HTTPTransportSandbox``, ``factory``). That keeps the pre-refactor boundary
intact: agent holds transport, sandbox holds zero transport.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import ClassVar

from deepagents.backends.protocol import (
    FileDownloadResponse,
    FileUploadResponse,
)

from decepticon.sandbox_kernel.base import SandboxBase
from decepticon.sandbox_kernel.jobs import BackgroundJobTracker


class DaemonSandbox(SandboxBase):
    """SandboxBase configured for direct, in-container execution.

    Args:
        container_name: Cosmetic identifier surfaced via ``.id`` and
            embedded in tmux session names / log files. Defaults to
            ``daemon`` because in the daemon's address space there is
            no docker-container concept to reach into.
        default_timeout: As :class:`SandboxBase`.
        workspace_path: Root of the agent-visible filesystem — set
            from ``SANDBOX_ROOT_DIR`` env (default ``/workspace``). In
            silo plane the gcsfuse mount narrows the actual filesystem
            view to a single engagement subtree; the daemon does NOT
            enforce this prefix itself.
    """

    _jobs: ClassVar[BackgroundJobTracker] = BackgroundJobTracker()
    _log_offsets: ClassVar[dict[str, int]] = {}
    _log_offsets_lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(
        self,
        container_name: str = "daemon",
        default_timeout: int = 120,
        workspace_path: str = "/workspace",
    ) -> None:
        # exec_prefix=[] turns every `[*self._exec_prefix, "tmux", ...]`
        # into `["tmux", ...]` etc — subprocess runs commands in this
        # process's own environment, no `docker exec` hop.
        super().__init__(
            container_name=container_name,
            default_timeout=default_timeout,
            workspace_path=workspace_path,
            exec_prefix=[],
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        # Path validation is intentionally absent — see module docstring
        # for the deploy-time trust model. Don't add prefix checks here
        # without also auditing the gcsfuse mount-isolation invariant.
        responses: list[FileUploadResponse] = []
        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            target = Path(path)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                responses.append(FileUploadResponse(path=path, error=None))
            except PermissionError:
                responses.append(FileUploadResponse(path=path, error="permission_denied"))
            except IsADirectoryError:
                responses.append(FileUploadResponse(path=path, error="is_directory"))
            except OSError:
                responses.append(FileUploadResponse(path=path, error="file_not_found"))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        # See upload_files note on absent path validation.
        responses: list[FileDownloadResponse] = []
        for path in paths:
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            source = Path(path)
            try:
                if source.is_dir():
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error="is_directory")
                    )
                    continue
                content = source.read_bytes()
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
            except FileNotFoundError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
            except PermissionError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="permission_denied")
                )
            except OSError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
        return responses
