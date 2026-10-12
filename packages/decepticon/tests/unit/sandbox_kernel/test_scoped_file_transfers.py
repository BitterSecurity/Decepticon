"""Real owned-file IO for the opt-in dedicated-root transfer proposal.

Fault injection covers native syscall/publication boundaries only. No sandbox
daemon, tmux session, target or network connection is started.
"""

from __future__ import annotations

import os
import stat
from collections import Counter
from pathlib import Path

import pytest

from decepticon.sandbox_kernel.scoped_file_transfers import (
    MAX_IMPORT_BYTES,
    MAX_OUTPUT_BYTES,
    BoundedOwnedDaemonSandbox,
    FileTransferError,
    OwnedFileTransferSandbox,
    RegeneratingOwnedSandbox,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="The daemon uses POSIX file descriptors")


@pytest.fixture
def owned(tmp_path: Path) -> tuple[Path, RegeneratingOwnedSandbox]:
    root = tmp_path / "owned"
    root.mkdir(mode=0o700)
    return root, RegeneratingOwnedSandbox(str(root))


@pytest.fixture
def descriptors(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    opened: list[int] = []
    closed: list[int] = []
    native_open, native_close = os.open, os.close

    def tracked_open(*args, **kwargs) -> int:
        fd = native_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def tracked_close(fd: int) -> None:
        native_close(fd)
        closed.append(fd)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    return opened, closed


def test_owned_nested_upload_download_and_regeneration(owned) -> None:
    root, sandbox = owned
    target = root / "reports" / "out.sarif"
    [upload] = sandbox.upload_files([(str(target), b"first")])
    assert upload.path == str(target) and upload.error is None
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    target.chmod(0o640)
    prior_inode = target.stat().st_ino
    [upload] = sandbox.upload_files([(str(target), b"second")])
    assert upload.error is None and target.stat().st_ino != prior_inode
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    [download] = sandbox.download_files([str(target)])
    assert download.path == str(target) and download.content == b"second"
    assert download.error is None
    assert not list(root.rglob(".owned-report-*"))


@pytest.mark.parametrize("suffix", ["relative.txt", "../outside", "/../outside", "/./out", "//out"])
def test_upload_and_download_refuse_noncanonical_paths(owned, suffix: str) -> None:
    root, sandbox = owned
    path = suffix if suffix.startswith("relative") else str(root) + suffix
    [upload] = sandbox.upload_files([(path, b"refused")])
    [download] = sandbox.download_files([path])
    assert upload.error and download.error and download.content is None
    assert not list(root.iterdir())


def test_foreign_absolute_and_sibling_prefix_are_refused(owned, tmp_path: Path) -> None:
    root, sandbox = owned
    for path in [str(tmp_path / "outside"), str(root) + "-other/out"]:
        assert sandbox.upload_files([(path, b"x")])[0].error == "foreign_path"
        assert sandbox.download_files([path])[0].error == "foreign_path"
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("kind", ["leaf", "parent", "root"])
def test_symlink_traversal_is_refused(owned, tmp_path: Path, kind: str) -> None:
    root, sandbox = owned
    outside = tmp_path / "outside"
    outside.mkdir()
    original = outside / "original"
    original.write_bytes(b"unchanged")
    if kind == "leaf":
        target = root / "link"
        target.symlink_to(original)
    elif kind == "parent":
        (root / "link").symlink_to(outside, target_is_directory=True)
        target = root / "link" / "original"
    else:
        link = tmp_path / "root-link"
        link.symlink_to(outside, target_is_directory=True)
        sandbox = RegeneratingOwnedSandbox(str(link))
        target = link / "original"
    assert sandbox.upload_files([(str(target), b"refused")])[0].error
    reply = sandbox.download_files([str(target)])[0]
    assert reply.error and reply.content is None
    assert original.read_bytes() == b"unchanged"


def test_multiple_links_are_refused_without_modifying_any_link(owned) -> None:
    root, sandbox = owned
    original = root / "original"
    original.write_bytes(b"unchanged")
    alias = root / "alias"
    os.link(original, alias)
    assert sandbox.download_files([str(alias)])[0].error == "unowned_file"
    assert sandbox.upload_files([(str(alias), b"refused")])[0].error == (
        "destination_not_owned_single_regular_file"
    )
    assert original.read_bytes() == alias.read_bytes() == b"unchanged"


def test_other_uid_is_refused_at_native_metadata_boundary(owned, monkeypatch) -> None:
    root, sandbox = owned
    target = root / "other-owner"
    target.write_bytes(b"unchanged")
    uid = target.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: uid + 1)
    assert sandbox.download_files([str(target)])[0].error == "unowned_file"
    assert sandbox.upload_files([(str(target), b"refused")])[0].error
    assert target.read_bytes() == b"unchanged"


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_leaf_is_refused_without_blocking(owned, kind: str) -> None:
    root, sandbox = owned
    target = root / "not-regular"
    if kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target, 0o600)
    assert sandbox.download_files([str(target)])[0].error == "not_regular_file"
    assert sandbox.upload_files([(str(target), b"refused")])[0].error


def test_read_limit_is_checked_before_and_during_read(owned, monkeypatch) -> None:
    root, _ = owned
    sandbox = BoundedOwnedDaemonSandbox(str(root), max_bytes=4)
    target = root / "input"
    target.write_bytes(b"large")
    assert sandbox.download_files([str(target)])[0].error == "file_too_large"
    target.write_bytes(b"fits")
    native_read = os.read
    grown = False

    def grow_then_read(fd: int, count: int) -> bytes:
        nonlocal grown
        if not grown:
            with target.open("ab") as stream:
                stream.write(b"+")
            grown = True
        return native_read(fd, count)

    monkeypatch.setattr(os, "read", grow_then_read)
    reply = sandbox.download_files([str(target)])[0]
    assert reply.error == "file_too_large" and reply.content is None


def test_default_import_and_output_byte_boundaries(owned) -> None:
    root, sandbox = owned
    target = root / "input"
    target.write_bytes(b"a" * MAX_IMPORT_BYTES)
    assert sandbox.download_files([str(target)])[0].content == target.read_bytes()
    target.write_bytes(b"a" * (MAX_IMPORT_BYTES + 1))
    assert sandbox.download_files([str(target)])[0].error == "file_too_large"
    output = root / "output"
    assert sandbox.upload_files([(str(output), b"a" * MAX_OUTPUT_BYTES)])[0].error is None
    assert sandbox.upload_files([(str(output), b"b" * (MAX_OUTPUT_BYTES + 1))])[0].error == (
        "file_too_large"
    )
    assert output.read_bytes() == b"a" * MAX_OUTPUT_BYTES


def test_changed_file_read_is_refused_and_descriptors_are_closed(
    owned, descriptors, monkeypatch
) -> None:
    root, sandbox = owned
    target = root / "input"
    target.write_bytes(b"first")
    native_read = os.read
    changed = False

    def changed_read(fd: int, count: int) -> bytes:
        nonlocal changed
        content = native_read(fd, count)
        if not changed:
            target.write_bytes(b"changed")
            changed = True
        return content

    monkeypatch.setattr(os, "read", changed_read)
    reply = sandbox.download_files([str(target)])[0]
    assert reply.error == "file_changed_during_read" and reply.content is None
    assert Counter(descriptors[0]) == Counter(descriptors[1])


@pytest.mark.parametrize("leaf", [".engagement", ".owned-report-forged.tmp"])
def test_reserved_owned_markers_are_refused(owned, leaf: str) -> None:
    root, sandbox = owned
    assert (
        sandbox.upload_files([(str(root / leaf), b"refused")])[0].error == "reserved_owned_marker"
    )
    assert not list(root.iterdir())


def test_one_file_budget_and_component_budget(owned) -> None:
    root, sandbox = owned
    paths = [str(root / "one"), str(root / "two")]
    assert all(reply.error == "request_budget" for reply in sandbox.download_files(paths))
    assert all(
        reply.error == "request_budget"
        for reply in sandbox.upload_files([(p, b"x") for p in paths])
    )
    assert (
        sandbox.upload_files([(str(root / "/".join(["deep"] * 9)), b"x")])[0].error
        == "invalid_path"
    )
    assert not list(root.iterdir())


def test_failed_prepublication_write_cleans_temporary_and_descriptors(
    owned, descriptors, monkeypatch
) -> None:
    root, sandbox = owned
    target = root / "output"

    def fail_write(fd: int, content: bytes) -> int:
        raise OSError("injected_write_failure")

    monkeypatch.setattr(os, "write", fail_write)
    reply = sandbox.upload_files([(str(target), b"value")])[0]
    assert reply.error and not reply.error.startswith("commit_outcome_unknown:")
    assert not target.exists() and not list(root.iterdir())
    assert Counter(descriptors[0]) == Counter(descriptors[1])


def test_new_file_publication_does_not_overwrite_competing_creator(owned, monkeypatch) -> None:
    root, sandbox = owned
    target = root / "output"
    native_link = os.link

    def competing_link(*args, **kwargs) -> None:
        target.write_bytes(b"competitor")
        native_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", competing_link)
    reply = sandbox.upload_files([(str(target), b"proposed")])[0]
    assert reply.error and not reply.error.startswith("commit_outcome_unknown:")
    assert target.read_bytes() == b"competitor" and not list(root.glob(".owned-report-*"))


def test_postpublication_directory_sync_failure_is_held(owned, monkeypatch) -> None:
    root, sandbox = owned
    target = root / "existing"
    target.write_bytes(b"old")
    native_fsync = os.fsync

    def fail_directory_sync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("injected_directory_sync_failure")
        native_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_directory_sync)
    reply = sandbox.upload_files([(str(target), b"new")])[0]
    assert reply.error.startswith("commit_outcome_unknown:")
    assert target.read_bytes() == b"new" and not list(root.glob(".owned-report-*"))


@pytest.mark.parametrize("committed", [False, True])
def test_cleanup_failure_is_a_structured_held_response(
    owned, descriptors, monkeypatch, committed: bool
) -> None:
    root, sandbox = owned
    target = root / "output"
    native_unlink = os.unlink

    def fail_temporary_unlink(path, *args, **kwargs) -> None:
        if str(path).startswith(".owned-report-"):
            raise PermissionError("injected_temporary_cleanup_failure")
        native_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fail_temporary_unlink)
    if not committed:

        def fail_write(fd: int, content: bytes) -> int:
            raise OSError("injected_prepublication_failure")

        monkeypatch.setattr(os, "write", fail_write)
    reply = sandbox.upload_files([(str(target), b"new")])[0]
    assert reply.error and "temporary_cleanup_failed" in reply.error
    assert reply.error.startswith("commit_outcome_unknown:") is committed
    assert target.exists() is committed
    assert len(list(root.glob(".owned-report-*"))) == 1
    assert Counter(descriptors[0]) == Counter(descriptors[1])


@pytest.mark.parametrize(
    "workspace", ["/workspace", "relative", "/workspace/a/b", "/workspace/../x"]
)
def test_opt_in_requires_a_dedicated_case_root(workspace: str) -> None:
    with pytest.raises(FileTransferError, match="dedicated owned case root"):
        OwnedFileTransferSandbox(workspace_path=workspace)


def test_opt_in_binds_a_valid_dedicated_case_root_without_starting_a_session() -> None:
    sandbox = OwnedFileTransferSandbox(workspace_path="/workspace/case-a")
    assert sandbox.allowed_root == "/workspace/case-a"
    assert sandbox.session_log_path("probe") == "/workspace/case-a/.sessions/probe.log"
