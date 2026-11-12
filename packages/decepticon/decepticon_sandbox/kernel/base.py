"""Compatibility adapter retaining the historical Deep Agents backend surface.

Execution-only daemons import sandbox_kernel.core instead, so their startup does
not initialize the agent graph just to use process/file response records.
"""

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse
from deepagents.backends.sandbox import BaseSandbox

from decepticon_sandbox.kernel.core import SandboxKernel


class SandboxBase(SandboxKernel, BaseSandbox):
    """Kernel execution plus the unchanged Deep Agents filesystem helpers."""

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return BaseSandbox.download_files(self, paths)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        result = super().execute(command, timeout=timeout)
        return ExecuteResponse(
            output=result.output, exit_code=result.exit_code, truncated=result.truncated
        )
