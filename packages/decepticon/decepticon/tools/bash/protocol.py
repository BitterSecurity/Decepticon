from __future__ import annotations

from typing import Protocol

from deepagents.backends.protocol import ExecuteResponse, FileUploadResponse

from decepticon.sandbox_kernel.jobs import BackgroundJob, BackgroundJobTracker


class BashSandboxProtocol(Protocol):
    _jobs: BackgroundJobTracker

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        raise NotImplementedError

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        raise NotImplementedError

    async def execute_tmux_async(
        self,
        command: str = "",
        session: str = "main",
        timeout: int | None = None,
        is_input: bool = False,
        workspace_path: str | None = None,
    ) -> str:
        raise NotImplementedError

    def start_background(
        self,
        command: str,
        session: str = "main",
        workspace_path: str | None = None,
    ) -> None:
        raise NotImplementedError

    def poll_completion(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> BackgroundJob | None:
        raise NotImplementedError

    def read_session_log_diff(
        self,
        session: str,
        workspace_path: str | None = None,
    ) -> str:
        raise NotImplementedError

    def kill_session(self, session: str, workspace_path: str | None = None) -> None:
        raise NotImplementedError

    def session_log_path(self, session: str, workspace_path: str | None = None) -> str:
        raise NotImplementedError


__all__ = ["BashSandboxProtocol"]
