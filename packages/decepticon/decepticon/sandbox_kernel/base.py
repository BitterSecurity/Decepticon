"""Shared sandbox implementation — used by both DockerSandbox
(agent-side, ``docker exec`` transport) and LocalSandbox (sandbox-side,
direct subprocess inside the daemon container).

`SandboxBase` holds every method that is invariant across the two
transports — tmux session management, workspace path normalization,
background-job tracking, log-offset diff, `execute()` (which already
uses `self._exec_prefix`), and the full tmux/background surface
(`execute_tmux`, `start_background`, `poll_completion`, etc.). The two
real backends below it implement just the file-transfer pair
(`upload_files`, `download_files`) — the only thing whose mechanics
genuinely differ between docker-exec (uses `docker cp`) and in-
container local execution (uses pathlib).

Class-level state (`_jobs`, `_log_offsets`) is intentionally redeclared
on each concrete subclass so the SandboxNotificationMiddleware's
`getattr(sandbox, "_jobs")` lookup pulls the subclass-specific tracker
rather than a shared base singleton.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import re
import subprocess
import threading
from typing import ClassVar

from deepagents.backends.protocol import ExecuteResponse
from deepagents.backends.sandbox import BaseSandbox

from decepticon.sandbox_kernel.jobs import BackgroundJob, BackgroundJobTracker
from decepticon.sandbox_kernel.tmux import (
    PS1_PATTERN,
    TmuxSessionManager,
    _safe_log,
    strip_terminal_noise,
)

log = logging.getLogger("decepticon.sandbox_kernel.base")

# ── Startup orphan-reap ──────────────────────────────────────────────────
#
# When the daemon is SIGKILLed, the tmux sessions/sockets it spawned via
# ``tmux -L <session> …`` (see TmuxSessionManager._tmux) survive the
# parent's death. A fresh daemon then starts with an empty manager map
# and `TmuxSessionManager._initialized` set, so it has no idea those
# servers exist and they accumulate forever — file-descriptor + memory
# leak, plus session-name collisions on the next engagement attempt.
#
# Reap discovers pre-existing sockets and kills the ones the new process
# does NOT own. The decision is split from the I/O so the unit tests can
# verify orphan classification without invoking real tmux (CI has none).
#
# Heuristic — we only reap sockets whose name carries the ``dcptn_``
# prefix produced by SandboxBase._tmux_session_name for non-root
# workspaces. Root-workspace sessions (bare names like ``main``) could
# belong to anything on a shared host and are left alone deliberately.

_DECEPTICON_TMUX_SOCKET_PREFIX = "dcptn_"


def _compute_orphan_tmux_sessions(
    discovered: set[str],
    tracked: set[str],
) -> set[str]:
    """Pure decision function for the startup reap.

    Returns the subset of ``discovered`` that are decepticon-owned
    (``dcptn_`` prefix) AND not present in ``tracked``. Tracked sessions
    are spared; non-decepticon-prefixed names are spared regardless.
    """
    return {
        name
        for name in discovered
        if name.startswith(_DECEPTICON_TMUX_SOCKET_PREFIX) and name not in tracked
    }


def _list_decepticon_tmux_sockets() -> list[str]:
    """List decepticon-owned tmux socket names on the host.

    Each ``tmux -L <name>`` creates a socket at ``/tmp/tmux-<uid>/<name>``
    (see ``man tmux``). We scan that directory and keep only the names
    matching the decepticon prefix so we never touch an unrelated user's
    tmux server on a shared host. Errors are logged + swallowed — a
    missing socket dir simply means no sessions to reap.
    """
    socket_dir = f"/tmp/tmux-{os.getuid()}"
    try:
        return [n for n in os.listdir(socket_dir) if n.startswith(_DECEPTICON_TMUX_SOCKET_PREFIX)]
    except FileNotFoundError:
        return []
    except OSError as e:
        log.debug("tmux socket directory probe failed: %s", _safe_log(e))
        return []


def _kill_tmux_socket(session: str) -> None:
    """Kill the tmux server backing a single decepticon socket.

    ``tmux -L <session> kill-server`` terminates the server (and reaps
    its bash children) for that named socket only — no other tmux server
    on the host is affected.
    """
    subprocess.run(
        ["tmux", "-L", session, "kill-server"],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
        encoding="utf-8",
        errors="replace",
    )


class SandboxBase(BaseSandbox):
    """deepagents BaseSandbox backed by a running Docker container.

    File operations (ls, read, write, edit, grep, glob) are handled by
    BaseSandbox, which delegates them to execute() — simple, non-interactive
    docker exec calls sufficient for atomic file ops.

    The bash tool uses execute_tmux() for persistent tmux sessions that
    support interactive input.

    ``_jobs`` and ``_log_offsets`` are class-level so every agent factory
    in a process talks to the same background-job tracker — the bash tool
    (which reads a module-global ``_sandbox`` set by whichever factory ran
    last) and the SandboxNotificationMiddleware (bound to a different
    instance per agent) would otherwise see disjoint trackers and
    completion notifications would never fire.
    """

    _jobs: ClassVar[BackgroundJobTracker] = BackgroundJobTracker()
    _log_offsets: ClassVar[dict[str, int]] = {}
    _log_offsets_lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(
        self,
        container_name: str = "decepticon-sandbox",
        default_timeout: int = 120,
        workspace_path: str = "/workspace",
        exec_prefix: list[str] | None = None,
    ) -> None:
        self._container_name = container_name
        self._default_timeout = default_timeout
        self._workspace_path = self._normalize_workspace_path(workspace_path)
        self._managers: dict[str, TmuxSessionManager] = {}
        self._managers_lock = threading.RLock()
        # Default to `[]` (local execution): the HTTP sandbox daemon runs
        # *inside* the sandbox container and drives tmux/exec directly.
        # The old `["docker", "exec", container_name]` default served the
        # retired DockerSandbox transport and is no longer reachable from
        # any production caller (HTTPSandbox is the agent-side client).
        self._exec_prefix: list[str] = list(exec_prefix) if exec_prefix is not None else []

    @staticmethod
    def _normalize_workspace_path(workspace_path: str | None) -> str:
        path = (workspace_path or "/workspace").strip()
        if path == "/workspace":
            return path
        if not path.startswith("/workspace/"):
            return "/workspace"
        path = path.rstrip("/")
        # Reject path traversal: a "." or ".." component passes the per-segment
        # character class below, so without this guard "/workspace/../../etc"
        # would be returned verbatim and escape the per-engagement subtree.
        # Mirror the EngagementFilesystem guard (middleware/filesystem.py): fail
        # closed to /workspace unless the path is already fully normalized.
        # posixpath (not os.path) because these are virtual POSIX paths —
        # os.path.normpath rewrites "/" to "\\" on Windows and would reject
        # every valid path.
        if posixpath.normpath(path) != path:
            return "/workspace"
        components = path[len("/workspace/") :].split("/")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", component) for component in components):
            return "/workspace"
        return path

    @staticmethod
    def _workspace_slug(workspace_path: str) -> str:
        path = SandboxBase._normalize_workspace_path(workspace_path)
        if path == "/workspace":
            return "root"
        digest = hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        slug = path.rsplit("/", 1)[-1] or "workspace"
        safe_slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", slug).strip("-") or "workspace"
        return f"{safe_slug}-{digest}"

    def _workspace_key(self, workspace_path: str | None = None) -> str:
        return self._workspace_slug(workspace_path or self._workspace_path)

    def _manager_key(self, session: str, workspace_path: str) -> str:
        if self._normalize_workspace_path(workspace_path) == "/workspace":
            return session
        return f"{self._workspace_key(workspace_path)}:{session}"

    @staticmethod
    def _tmux_session_name(session: str, workspace_path: str) -> str:
        if SandboxBase._normalize_workspace_path(workspace_path) == "/workspace":
            return SandboxBase._safe_session_name(session)
        workspace_key = SandboxBase._workspace_slug(workspace_path)
        safe_session = SandboxBase._safe_session_name(session)
        return f"dcptn_{workspace_key}_{safe_session}"

    @staticmethod
    def _safe_session_name(session: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_.-]+", "-", session).strip("-") or "main"

    def session_log_path(self, session: str, workspace_path: str | None = None) -> str:
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        return f"{effective_workspace}/.sessions/{self._safe_session_name(session)}.log"

    def _get_manager(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> TmuxSessionManager:
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        key = self._manager_key(session, effective_workspace)
        tmux_session = self._tmux_session_name(session, effective_workspace)
        log_name = f"{self._safe_session_name(session)}"
        with self._managers_lock:
            if key not in self._managers:
                self._managers[key] = TmuxSessionManager(
                    tmux_session,
                    self._container_name,
                    workspace_path=effective_workspace,
                    log_name=log_name,
                    exec_prefix=self._exec_prefix,
                )
            return self._managers[key]

    # ── BaseSandbox abstract methods ──────────────────────────────────────

    @property
    def id(self) -> str:
        return self._container_name

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Simple docker exec — used by BaseSandbox for file operations."""
        effective = timeout if timeout is not None else self._default_timeout
        try:
            result = subprocess.run(
                [*self._exec_prefix, "sh", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective,
                encoding="utf-8",
                errors="replace",
            )
            output = result.stdout
            if result.stderr and result.stderr.strip():
                output += f"\n<stderr>{result.stderr.strip()}</stderr>"
            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=False,
            )
        except subprocess.TimeoutExpired:
            return ExecuteResponse(
                output=f"Command timed out after {effective}s",
                exit_code=124,
                truncated=False,
            )

    def read_session_log_diff(self, session: str, workspace_path: str | None = None) -> str:
        """Return new bytes appended to <workspace>/.sessions/<session>.log
        since the previous call (or the whole file on first call).

        Per-process offset tracking only — restart resets to 0 (safe fallback).
        File truncation/rotation also resets to 0.
        """
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        key = self._manager_key(session, effective_workspace)
        log_path = self.session_log_path(session, effective_workspace)
        results = self.download_files([log_path])
        if not results or results[0].error or results[0].content is None:
            return ""
        full = results[0].content
        with self._log_offsets_lock:
            prev_offset = self._log_offsets.get(key, 0)
            if prev_offset > len(full):
                prev_offset = 0
            new_bytes = full[prev_offset:]
            self._log_offsets[key] = len(full)
        # Strip terminal controls, but keep prompt-shaped text: program output
        # can contain the same text, and native state now owns completion.
        return strip_terminal_noise(
            new_bytes.decode("utf-8", errors="replace"), preserve_prompt_markers=True
        )

    def reset_session_log_offset(self, session: str, workspace_path: str | None = None) -> None:
        """Forget the read offset (used after kill / GC)."""
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        key = self._manager_key(session, effective_workspace)
        with self._log_offsets_lock:
            self._log_offsets.pop(key, None)

    # ── Tmux execution (for bash tool) ───────────────────────────────────

    def execute_tmux(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str:
        """Tmux-based execution with session persistence and interactive support.

        Used exclusively by the bash tool. Supports:
        - Named sessions for parallel command execution
        - Interactive input (y/n, passwords, C-c / C-z / C-d)
        - Output truncation for large outputs
        """
        effective = timeout if timeout is not None else self._default_timeout
        mgr = self._get_manager(session, workspace_path)

        if not command and not is_input:
            return mgr.read_screen()

        return mgr.execute(
            command,
            is_input=is_input,
            timeout=effective,
        )

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str:
        """Async tmux execution — cancellable via asyncio.CancelledError.

        Used by the async bash tool so that LangGraph run cancellation
        (Ctrl+C → cancelMany) interrupts the polling loop promptly.
        """
        effective = timeout if timeout is not None else self._default_timeout
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        job_key = self._manager_key(session, effective_workspace)
        mgr = self._get_manager(session, effective_workspace)

        if not command and not is_input:
            return await asyncio.to_thread(mgr.read_screen)

        def _on_auto_background(cmd: str, baseline: str) -> None:
            job = self._jobs.register(
                session,
                command=cmd,
                initial_markers=len(PS1_PATTERN.findall(baseline)),
                key=job_key,
                workspace_path=effective_workspace,
            )
            job.generation = mgr._generation

        return await mgr.execute_async(
            command,
            is_input=is_input,
            timeout=effective,
            on_auto_background=_on_auto_background,
        )

    def start_background(
        self,
        command: str,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        """Launch a command in a named tmux session without blocking.

        Binds the BackgroundJob to the submitted native command generation.
        """
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        job_key = self._manager_key(session, effective_workspace)
        mgr = self._get_manager(session, effective_workspace)
        mgr.initialize()
        baseline = mgr._capture()
        initial_markers = len(PS1_PATTERN.findall(baseline))
        generation = mgr._begin_command(command)
        job = self._jobs.register(
            session,
            command=command,
            initial_markers=initial_markers,
            key=job_key,
            workspace_path=effective_workspace,
        )
        job.generation = generation

    def poll_completion(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> "BackgroundJob | None":
        """Check whether the background job's native command has completed.

        Updates the tracker in place; returns the job (or None if not tracked).
        Capture failures are swallowed — the job stays running, retried later.
        """
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        job_key = self._manager_key(session, effective_workspace)
        job = self._jobs.get(session, key=job_key)
        if job is None or job.status != "running":
            return job
        try:
            mgr = self._get_manager(session, effective_workspace)
            completion = mgr._command_completion(job.generation)
        except (RuntimeError, OSError, subprocess.TimeoutExpired):
            return job
        if completion is not None:
            self._jobs.mark_complete(session, exit_code=completion[0], key=job_key)
        return job

    def kill_session(self, session: str, workspace_path: str | None = None) -> None:
        """Send Ctrl+C, then kill the tmux session, then clear all caches.

        Best-effort: errors are logged, not raised. The pipe-pane log file
        is preserved at <engagement>/.sessions/<session>.log for audit.
        """
        effective_workspace = self._normalize_workspace_path(workspace_path or self._workspace_path)
        manager_key = self._manager_key(session, effective_workspace)
        mgr: TmuxSessionManager | None = None
        try:
            mgr = self._get_manager(session, effective_workspace)
            try:
                mgr._tmux(["send-keys", "-t", mgr.session, "C-c"])
            except RuntimeError as e:
                log.debug("send-keys C-c failed for '%s': %s", _safe_log(session), _safe_log(e))
            try:
                mgr._tmux(["kill-session", "-t", mgr.session])
            except RuntimeError as e:
                log.warning("kill-session failed for '%s': %s", _safe_log(session), _safe_log(e))
        finally:
            with self._managers_lock:
                self._managers.pop(manager_key, None)
            if mgr is not None:
                with TmuxSessionManager._init_lock:
                    TmuxSessionManager._initialized.discard(mgr.session)
            self.reset_session_log_offset(session, effective_workspace)
            self._jobs.remove(session, key=manager_key)

    def reap_orphaned_tmux_sessions(self) -> int:
        """Kill decepticon-named tmux sockets this process does NOT own.

        Wired into the daemon's startup lifespan: if the previous daemon
        was SIGKILLed, its tmux sockets/sessions survive but no live
        manager tracks them. Without this reap they leak forever.

        Returns the count of sockets successfully killed. Discovery and
        kill go through module-level seams (``_list_decepticon_tmux_sockets``,
        ``_kill_tmux_socket``) so unit tests can drive the logic without
        invoking real tmux.
        """
        discovered = set(_list_decepticon_tmux_sockets())
        with self._managers_lock:
            tracked = {mgr.session for mgr in self._managers.values()}
        orphans = _compute_orphan_tmux_sessions(discovered, tracked)
        killed = 0
        for session in orphans:
            try:
                _kill_tmux_socket(session)
                killed += 1
            except (
                subprocess.CalledProcessError,
                subprocess.TimeoutExpired,
                OSError,
                RuntimeError,
            ) as e:
                log.warning(
                    "reap: failed to kill orphan tmux socket '%s': %s",
                    _safe_log(session),
                    _safe_log(e),
                )
        if killed:
            log.info("reap: killed %d orphan tmux socket(s) on startup", killed)
        return killed

    def kill_all_sessions(self) -> int:
        """Kill every tmux session this sandbox has handed out a manager for.

        Returns the number of sessions kill-session was invoked on. Used by
        the sandbox daemon's shutdown hook to make sure tmux servers don't
        outlive the daemon process and leave behind zombie ``bash``/``tmux``
        children when their parent disappears.

        Best-effort: every kill is wrapped in try/except so one stuck
        session doesn't block the rest from being torn down.
        """
        with self._managers_lock:
            managers = list(self._managers.values())
            keys = list(self._managers.keys())
            self._managers.clear()
        killed = 0
        for mgr in managers:
            try:
                # Each TmuxSessionManager runs its own ``tmux -L <name>``
                # socket (see TmuxSessionManager._tmux), so kill-server
                # terminates exactly the tmux process backing this session
                # — including reaping the bash child the new-session
                # command spawned. This is more thorough than kill-session
                # for the shutdown path: even if the session was already
                # detached, the tmux server itself exits.
                mgr._tmux(["kill-server"], timeout=5)
                killed += 1
            except (RuntimeError, subprocess.TimeoutExpired, OSError) as e:
                log.debug(
                    "kill-server failed for session '%s' during shutdown: %s",
                    _safe_log(mgr.session),
                    _safe_log(e),
                )
        # Reset all per-session tracking state alongside the manager
        # cache so a subsequent restart starts from a clean slate.
        with TmuxSessionManager._init_lock:
            for mgr in managers:
                TmuxSessionManager._initialized.discard(mgr.session)
        for key in keys:
            with self._log_offsets_lock:
                self._log_offsets.pop(key, None)
        return killed


# ─── Pre-flight check ────────────────────────────────────────────────────
