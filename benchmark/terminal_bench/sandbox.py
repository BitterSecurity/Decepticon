from __future__ import annotations

import os
import shlex
import tempfile
import uuid
from pathlib import PurePosixPath

import anyio
from anyio import from_thread
from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse
from harbor.agents.terminus_2.tmux_session import TmuxSession
from harbor.environments.base import BaseEnvironment

from decepticon.sandbox_kernel.jobs import BackgroundJob, BackgroundJobTracker
from decepticon.sandbox_kernel.tmux import strip_terminal_noise


def _command_with_exit_marker(command: str, marker: str) -> str:
    return f"{command}\nprintf '%s' $? > {shlex.quote(marker)}"


class HarborSandboxAdapter:
    def __init__(self, environment: BaseEnvironment) -> None:
        self._environment = environment
        self._sessions: dict[str, TmuxSession] = {}
        self._background_markers: dict[str, str] = {}
        self._jobs = BackgroundJobTracker()

    @staticmethod
    def _safe_session(session: str) -> str:
        safe = "".join(char if char.isalnum() or char in "_.-" else "-" for char in session)
        return safe.strip("-") or "main"

    def _tmux_name(self, session: str) -> str:
        environment_id = self._safe_session(self._environment.session_id)[-40:]
        return f"dcptn-{environment_id}-{self._safe_session(session)}"[-80:]

    async def _get_session(self, session: str) -> TmuxSession:
        safe = self._safe_session(session)
        existing = self._sessions.get(safe)
        if existing is not None and await existing.is_session_alive():
            return existing
        tmux = TmuxSession(
            session_name=self._tmux_name(safe),
            environment=self._environment,
            logging_path=PurePosixPath(f"/tmp/decepticon-{safe}.log"),
            local_asciinema_recording_path=None,
            remote_asciinema_recording_path=None,
            extra_env=self._environment_env,
            user=self._environment.default_user,
        )
        await tmux.start()
        self._sessions[safe] = tmux
        return tmux

    @property
    def _environment_env(self) -> dict[str, str]:
        return {"DECEPTICON_BENCHMARK": "terminal-bench-2.1"}

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str:
        del workspace_path
        tmux = await self._get_session(session)
        if not command and not is_input:
            return strip_terminal_noise(await tmux.capture_pane(capture_entire=False))
        if is_input:
            keys = [command] if command.startswith("C-") else [command, "Enter"]
            await tmux.send_keys(keys, min_timeout_sec=0.2)
            return strip_terminal_noise(await tmux.capture_pane(capture_entire=False))

        baseline = await tmux.capture_pane(capture_entire=True)
        marker = f"/tmp/decepticon-command-{uuid.uuid4().hex}.done"
        wrapped = _command_with_exit_marker(command, marker)
        await tmux.send_keys([wrapped, "Enter"])
        effective_timeout = timeout if timeout is not None else 120
        blocking_timeout = min(effective_timeout, 60)
        try:
            with anyio.fail_after(blocking_timeout):
                while True:
                    result = await self._environment.exec(
                        f"test -f {shlex.quote(marker)} && cat {shlex.quote(marker)}"
                    )
                    if result.return_code == 0 and result.stdout is not None:
                        screen = await tmux.capture_pane(capture_entire=True)
                        await self._environment.exec(f"rm -f {shlex.quote(marker)}")
                        return self._command_output(screen, baseline, result.stdout.strip())
                    await anyio.sleep(0.1)
        except TimeoutError:
            if effective_timeout >= 60:
                self._background_markers[session] = marker
                self._jobs.register(session, command, initial_markers=0)
                return (
                    f"[AUTO-BACKGROUND] Command exceeded 60s in session '{session}' "
                    "and is still running. Continue useful work in another session."
                )
            return (
                f"[TIMEOUT] Command exceeded {effective_timeout}s in session '{session}'. "
                "The session is still occupied; inspect it with an empty bash command."
            )

    @staticmethod
    def _command_output(screen: str, baseline: str, exit_text: str) -> str:
        current = screen[len(baseline) :] if screen.startswith(baseline) else screen
        lines = current.splitlines()
        output = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
        clean = strip_terminal_noise(output)
        if clean:
            return clean
        return f"[Command completed with no output. Exit code: {exit_text}]"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        async def run() -> ExecuteResponse:
            result = await self._environment.exec(command, timeout_sec=timeout)
            output = result.stdout or ""
            if result.stderr:
                output += f"\n<stderr>{result.stderr.strip()}</stderr>"
            return ExecuteResponse(output=output, exit_code=result.return_code, truncated=False)

        return from_thread.run(run)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for target, content in files:
            handle = tempfile.NamedTemporaryFile(delete=False)
            try:
                handle.write(content)
                handle.close()

                async def upload() -> None:
                    await self._environment.upload_file(handle.name, target)

                from_thread.run(upload)
                responses.append(FileUploadResponse(path=target))
            except PermissionError:
                responses.append(FileUploadResponse(path=target, error="permission_denied"))
            except OSError:
                responses.append(FileUploadResponse(path=target, error="invalid_path"))
            finally:
                handle.close()
                os.unlink(handle.name)
        return responses

    def start_background(
        self,
        command: str,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        del workspace_path
        marker = f"/tmp/decepticon-job-{uuid.uuid4().hex}.done"

        async def start() -> None:
            tmux = await self._get_session(session)
            wrapped = _command_with_exit_marker(command, marker)
            await tmux.send_keys([wrapped, "Enter"])

        from_thread.run(start)
        self._background_markers[session] = marker
        self._jobs.register(session, command, initial_markers=0)

    def poll_completion(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> BackgroundJob | None:
        del workspace_path
        job = self._jobs.get(session)
        marker = self._background_markers.get(session)
        if job is None or job.status != "running" or marker is None:
            return job

        async def poll() -> int | None:
            result = await self._environment.exec(
                f"test -f {shlex.quote(marker)} && cat {shlex.quote(marker)}"
            )
            if result.return_code != 0 or result.stdout is None:
                return None
            try:
                return int(result.stdout.strip())
            except ValueError:
                return -1

        exit_code = from_thread.run(poll)
        if exit_code is not None:
            self._jobs.mark_complete(session, exit_code)
        return job

    def read_session_log_diff(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> str:
        del workspace_path

        async def read() -> str:
            tmux = await self._get_session(session)
            return strip_terminal_noise(await tmux.get_incremental_output())

        return from_thread.run(read)

    def kill_session(self, session: str, workspace_path: str | None = None) -> None:
        del workspace_path
        safe_name = self._tmux_name(session)

        async def kill() -> None:
            await self._environment.exec(f"tmux kill-session -t {shlex.quote(safe_name)}")

        from_thread.run(kill)
        self._sessions.pop(self._safe_session(session), None)
        self._jobs.remove(session)

    def session_log_path(self, session: str, workspace_path: str | None = None) -> str:
        del workspace_path
        return f"/tmp/decepticon-{self._safe_session(session)}.log"


__all__ = ["HarborSandboxAdapter"]
