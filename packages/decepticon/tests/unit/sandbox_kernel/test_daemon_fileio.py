"""File-IO tests for ``DaemonSandbox`` (``upload_files`` / ``download_files``).

The daemon runs only inside the Linux sandbox container and addresses files
by POSIX-absolute ``/workspace`` paths — it rejects anything not starting
with ``/``. On Windows ``tmp_path`` is drive-rooted, so these real-filesystem
tests are POSIX-only (see the module-level skip).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from decepticon_sandbox.kernel.daemon import DaemonSandbox

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="DaemonSandbox file IO uses POSIX-absolute paths; the daemon runs "
    "only inside the Linux sandbox container",
)


# ── upload_files ────────────────────────────────────────────────────────


def test_upload_writes_file(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    [resp] = DaemonSandbox().upload_files([(str(target), b"hello")])

    assert resp.error is None
    assert target.read_bytes() == b"hello"


def test_upload_creates_missing_parent_directories(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "out.txt"

    [resp] = DaemonSandbox().upload_files([(str(target), b"x")])

    assert resp.error is None
    assert target.read_bytes() == b"x"


def test_upload_rejects_relative_path() -> None:
    [resp] = DaemonSandbox().upload_files([("relative/out.txt", b"x")])

    assert resp.error == "invalid_path"


def test_upload_reports_permission_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _denied(self: Path, data: bytes) -> int:
        raise PermissionError

    monkeypatch.setattr(Path, "write_bytes", _denied)

    [resp] = DaemonSandbox().upload_files([(str(tmp_path / "out.txt"), b"x")])

    assert resp.error == "permission_denied"


def test_upload_reports_is_directory(tmp_path: Path) -> None:
    existing_dir = tmp_path / "a-directory"
    existing_dir.mkdir()

    [resp] = DaemonSandbox().upload_files([(str(existing_dir), b"x")])

    assert resp.error == "is_directory"


def test_upload_batch_returns_one_ordered_response_per_input(tmp_path: Path) -> None:
    files = [(str(tmp_path / f"f{i}.txt"), f"c{i}".encode()) for i in range(3)]

    responses = DaemonSandbox().upload_files(files)

    assert [r.path for r in responses] == [p for p, _ in files]
    assert all(r.error is None for r in responses)


def test_upload_empty_list() -> None:
    assert DaemonSandbox().upload_files([]) == []


# ── download_files ──────────────────────────────────────────────────────


def test_download_reads_file(tmp_path: Path) -> None:
    source = tmp_path / "in.txt"
    source.write_bytes(b"payload")

    [resp] = DaemonSandbox().download_files([str(source)])

    assert resp.error is None
    assert resp.content == b"payload"


def test_download_round_trips_an_upload(tmp_path: Path) -> None:
    sandbox = DaemonSandbox()
    target = str(tmp_path / "rt.bin")

    sandbox.upload_files([(target, b"\x00\x01\x02round")])
    [resp] = sandbox.download_files([target])

    assert resp.content == b"\x00\x01\x02round"


def test_download_missing_file_reports_not_found(tmp_path: Path) -> None:
    [resp] = DaemonSandbox().download_files([str(tmp_path / "absent.txt")])

    assert resp.content is None
    assert resp.error == "file_not_found"


def test_download_rejects_relative_path() -> None:
    [resp] = DaemonSandbox().download_files(["relative.txt"])

    assert resp.error == "invalid_path"


def test_download_directory_reports_is_directory(tmp_path: Path) -> None:
    [resp] = DaemonSandbox().download_files([str(tmp_path)])

    assert resp.content is None
    assert resp.error == "is_directory"


def test_download_batch_returns_one_ordered_response_per_input(tmp_path: Path) -> None:
    sources: list[str] = []
    for i in range(3):
        path = tmp_path / f"d{i}.txt"
        path.write_bytes(f"v{i}".encode())
        sources.append(str(path))

    responses = DaemonSandbox().download_files(sources)

    assert [r.path for r in responses] == sources
    assert [r.content for r in responses] == [b"v0", b"v1", b"v2"]


def test_download_empty_list() -> None:
    assert DaemonSandbox().download_files([]) == []


# ── construction ────────────────────────────────────────────────────────


def test_daemon_uses_empty_exec_prefix() -> None:
    """The in-container daemon runs subprocesses directly — no docker-exec hop."""
    assert DaemonSandbox()._exec_prefix == []
