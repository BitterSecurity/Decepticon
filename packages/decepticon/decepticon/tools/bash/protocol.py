from __future__ import annotations

from typing import Protocol

from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse

from decepticon.sandbox_kernel.jobs import BackgroundJob, BackgroundJobTracker


class BashSandboxProtocol(Protocol):
    _jobs: BackgroundJobTracker

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse: ...

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]: ...

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str: ...

    def start_background(
        self,
        command: str,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None: ...

    def poll_completion(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> BackgroundJob | None: ...

    def read_session_log_diff(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> str: ...

    def kill_session(self, session: str, workspace_path: str | None = None) -> None: ...

    def session_log_path(self, session: str, workspace_path: str | None = None) -> str: ...


__all__ = ["BashSandboxProtocol"]
