"""Framework-independent records for the execution daemon's existing wire API.

Agent transports translate these fields into the real Deep Agents records. The
daemon must not initialize agent graphs/providers to return process/file data.
"""

from dataclasses import dataclass
from typing import Protocol


class ExecutionResult(Protocol):
    output: str
    exit_code: int | None
    truncated: bool


class DownloadResult(Protocol):
    path: str
    content: bytes | None
    error: str | None


@dataclass
class ExecuteResponse:
    output: str
    exit_code: int | None = None
    truncated: bool = False


@dataclass
class FileUploadResponse:
    path: str
    error: str | None = None


@dataclass
class FileDownloadResponse:
    path: str
    content: bytes | None = None
    error: str | None = None
