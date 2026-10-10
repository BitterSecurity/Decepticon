"""Opt-in transfers for a dedicated owned engagement sandbox process.

Not a shared-root policy. Original execute/lifecycle methods are inherited;
they must only run inside the separately reserved owned guest/service scope.
"""
import os
from pathlib import PurePosixPath
import stat
import threading
import uuid
from deepagents.backends.protocol import FileDownloadResponse, FileUploadResponse
from decepticon.sandbox_kernel.daemon import DaemonSandbox
from decepticon_core.utils.engagement_scope import is_valid_engagement_label

MAX_IMPORT_BYTES = 65536
MAX_OUTPUT_BYTES = 1048576

class FileTransferError(ValueError):
    """A transfer violates the configured dedicated-root boundary."""


class BoundedOwnedDaemonSandbox(DaemonSandbox):
    """Candidate daemon read guard; fixed operator root, no symlink traversal.

    Actual FD traversal pins directories; size checked before and during reading.
    No execute/tmux/cleanup method is used by the integration tests.
    """
    def __init__(self, allowed_root: str, max_bytes: int = MAX_IMPORT_BYTES, *, container_name: str = "daemon", default_timeout: int = 120) -> None:
        super().__init__(workspace_path=allowed_root, container_name=container_name, default_timeout=default_timeout)
        self.allowed_root = allowed_root
        self.max_bytes = max_bytes

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        if len(paths) != 1:
            return [FileDownloadResponse(path=p, content=None, error='request_budget') for p in paths]
        results = []
        for path in paths:
            descriptor = None
            try:
                prefix = self.allowed_root.rstrip('/') + '/'
                if not isinstance(path, str) or not path.startswith(prefix):
                    raise FileTransferError('foreign_path')
                suffix = path[len(prefix):]
                components = suffix.split('/')
                if not suffix or any(c in {'', '.', '..'} for c in components) or str(PurePosixPath(path)) != path:
                    raise FileTransferError('invalid_path')
                descriptor = os.open(self.allowed_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                for component in components[:-1]:
                    following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = following
                file_descriptor = os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
                try:
                    info = os.fstat(file_descriptor)
                    if not stat.S_ISREG(info.st_mode):
                        raise FileTransferError('not_regular_file')
                    if info.st_uid != os.geteuid() or info.st_nlink != 1:
                        raise FileTransferError('unowned_file')
                    if info.st_size > self.max_bytes:
                        raise FileTransferError('file_too_large')
                    chunks = []
                    total = 0
                    while True:
                        chunk = os.read(file_descriptor, min(8192, self.max_bytes + 1 - total))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise FileTransferError('file_too_large')
                        chunks.append(chunk)
                    after = os.fstat(file_descriptor)
                    if (after.st_uid != os.geteuid() or after.st_nlink != 1
                            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                            != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)):
                        raise FileTransferError('file_changed_during_read')
                    content = b''.join(chunks)
                finally:
                    os.close(file_descriptor)
                results.append(FileDownloadResponse(path=path, content=content, error=None))
            except (ValueError, OSError) as exc:
                results.append(FileDownloadResponse(path=path, content=None, error=str(exc)))
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        return results


class RegeneratingOwnedSandbox(BoundedOwnedDaemonSandbox):
    """One owned endpoint root; atomic replacement of its owned regular files."""
    def __init__(self, allowed_root: str, *, container_name: str = "daemon", default_timeout: int = 120) -> None:
        super().__init__(allowed_root, container_name=container_name, default_timeout=default_timeout)
        self._report_upload_lock = threading.RLock()

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if len(files) != 1:
            return [FileUploadResponse(path=p, error='request_budget') for p, _ in files]
        path, content = files[0]
        with self._report_upload_lock:
            return [self._upload_one(path, content)]

    def _upload_one(self, path: str, content: bytes) -> FileUploadResponse:
        directory_fd = temporary_fd = None
        temporary_name = None
        committed = False
        try:
            prefix = self.allowed_root.rstrip('/') + '/'
            if not isinstance(path, str) or not path.startswith(prefix):
                raise FileTransferError('foreign_path')
            suffix = path[len(prefix):]
            components = suffix.split('/')
            if not suffix or len(components) > 8 or any(c in {'', '.', '..'} or '\x00' in c for c in components) or str(PurePosixPath(path)) != path:
                raise FileTransferError('invalid_path')
            if suffix == '.engagement' or any(c.startswith('.owned-report-') for c in components):
                raise FileTransferError('reserved_owned_marker')
            if not isinstance(content, bytes) or len(content) > MAX_OUTPUT_BYTES:
                raise FileTransferError('file_too_large')
            directory_fd = os.open(self.allowed_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for component in components[:-1]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                following = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = following
            name = components[-1]
            try:
                prior = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                prior = None
            if prior is not None and (not stat.S_ISREG(prior.st_mode) or prior.st_uid != os.geteuid() or prior.st_nlink != 1):
                raise FileTransferError('destination_not_owned_single_regular_file')
            temporary_name = '.owned-report-' + uuid.uuid4().hex + '.tmp'
            temporary_fd = os.open(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            if prior is not None:
                os.fchmod(temporary_fd, stat.S_IMODE(prior.st_mode))
            remaining = memoryview(content)
            while remaining:
                written = os.write(temporary_fd, remaining)
                if written <= 0:
                    raise OSError('short_write')
                remaining = remaining[written:]
            os.fsync(temporary_fd)
            try:
                latest = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                latest = None
            def identity(info: os.stat_result | None) -> tuple[int, int, int, int] | None:
                return None if info is None else (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            if identity(latest) != identity(prior):
                raise FileTransferError('destination_changed_during_write')
            if prior is None:
                # Exclusive publication for a new path; another creator is not
                # overwritten between the final check and this commit.
                os.link(temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
                committed = True
                os.unlink(temporary_name, dir_fd=directory_fd)
            else:
                # Existing regular leaf replaced atomically. A native external
                # writer can still race after the check; no universal CAS claim.
                os.replace(temporary_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                committed = True
            temporary_name = None
            os.fsync(directory_fd)
            return FileUploadResponse(path=path, error=None)
        except (OSError, ValueError) as exc:
            return FileUploadResponse(path=path, error=('commit_outcome_unknown:' if committed else '') + str(exc))
        finally:
            if temporary_fd is not None:
                os.close(temporary_fd)
            if temporary_name is not None and directory_fd is not None:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
            if directory_fd is not None:
                os.close(directory_fd)



class OwnedFileTransferSandbox(RegeneratingOwnedSandbox):
    def __init__(self, *, container_name: str = "daemon", default_timeout: int = 120, workspace_path: str) -> None:
        prefix = "/workspace/"
        if not isinstance(workspace_path, str) or not workspace_path.startswith(prefix) or not is_valid_engagement_label(workspace_path[len(prefix):]):
            raise FileTransferError("bounded transfers require a dedicated owned case root")
        super().__init__(workspace_path, container_name=container_name, default_timeout=default_timeout)
