"""Background-job tracking — used by the bash tool / SandboxNotificationMiddleware.

Originally lived inline in ``decepticon/backends/docker_sandbox.py``. Moved
out so the in-container HTTP daemon (``decepticon.sandbox_server``) can
import it without pulling in the agent-side ``DockerSandbox`` /
``HTTPSandbox`` transport classes — see ``sandbox_kernel/__init__.py``
for the layering rationale.
"""

from __future__ import annotations

import dataclasses
import threading
import time


@dataclasses.dataclass
class BackgroundJob:
    """Metadata for one background command in a tmux session.

    A session holds at most one BackgroundJob — sequential reuse replaces.
    Timestamps use ``time.monotonic()`` so elapsed values stay correct
    across wall-clock adjustments (NTP step, manual ``date -s``).
    """

    session: str
    key: str
    command: str
    initial_markers: int
    started_at: float
    workspace_path: str = "/workspace"
    status: str = "running"  # running | done
    exit_code: int | None = None
    completed_at: float | None = None
    consumed: bool = False
    generation: str | None = None

    @property
    def elapsed(self) -> float:
        end = self.completed_at if self.completed_at is not None else time.monotonic()
        return end - self.started_at


class BackgroundJobTracker:
    """In-memory background-job registry keyed by session name."""

    def __init__(self) -> None:
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = threading.RLock()

    def register(
        self,
        session: str,
        command: str,
        initial_markers: int,
        key: str | None = None,
        workspace_path: str = "/workspace",
    ) -> BackgroundJob:
        job_key = key or session
        with self._lock:
            job = BackgroundJob(
                session=session,
                key=job_key,
                workspace_path=workspace_path,
                command=command,
                initial_markers=initial_markers,
                started_at=time.monotonic(),
            )
            self._jobs[job_key] = job
            return job

    def get(self, session: str, key: str | None = None) -> BackgroundJob | None:
        with self._lock:
            return self._jobs.get(key or session)

    def mark_complete(self, session: str, exit_code: int, key: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(key or session)
            if job is None or job.status != "running":
                return
            job.status = "done"
            job.exit_code = exit_code
            job.completed_at = time.monotonic()

    def mark_consumed(self, session: str, key: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(key or session)
            if job is not None:
                job.consumed = True

    def pending_completions(self) -> list[BackgroundJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "done" and not j.consumed]

    def all_jobs(self) -> list[BackgroundJob]:
        with self._lock:
            return list(self._jobs.values())

    def remove(self, session: str, key: str | None = None) -> None:
        with self._lock:
            self._jobs.pop(key or session, None)
