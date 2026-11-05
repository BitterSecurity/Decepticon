"""Fixed-path startup inside an admitted workload, never a host executor.

Invoke by absolute script path with Python -I. The trusted image bundle is
inserted explicitly; a writable workspace cannot shadow its implementation.
The scope copy is agent-readable context, not host authorization.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
ROOT = Path("/workspace")
STATE = Path("/tmp/decepticon-session")


def restore_scope(root: Path, content: bytes) -> None:
    """Only replace the fixed scope file through no-follow directory handles."""
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir("plan", mode=0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        plan_fd = os.open("plan", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        try:
            name = ".roe-" + secrets.token_hex(12)
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444, dir_fd=plan_fd
            )
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(name, "roe.json", src_dir_fd=plan_fd, dst_dir_fd=plan_fd)
                os.fsync(plan_fd)
            finally:
                try:
                    os.unlink(name, dir_fd=plan_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(plan_fd)
    finally:
        os.close(root_fd)


def process_identity(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return None if fields[0] in ("Z", "X") else fields[19]
    except (OSError, IndexError):
        return None


def bootstrap(message: dict, *, root: Path = ROOT, state: Path = STATE) -> dict:
    if set(message) != {"roe_b64", "roe_sha256"}:
        raise ValueError("Only a fixed approved scope payload is accepted")
    content = base64.b64decode(message["roe_b64"], validate=True)
    if len(content) > MAX_BYTES or hashlib.sha256(content).hexdigest() != message["roe_sha256"]:
        raise ValueError("Approved scope checksum mismatch")
    if not isinstance(json.loads(content), dict):
        raise ValueError("Approved scope must be a JSON object")
    state.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        lock = os.open(
            "startup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=directory
        )
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            restore_scope(root, content)
            try:
                fd = os.open("daemon.pid", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd) as input_file:
                    existing = json.loads(input_file.read(128))
                pid = existing["pid"]
                if (
                    isinstance(pid, int)
                    and pid > 1
                    and isinstance(existing["start"], str)
                    and process_identity(pid) == existing["start"]
                ):
                    return {"pid": pid, "start": existing["start"], "reused": True}
            except FileNotFoundError:
                pass
            log = os.open(
                "daemon.log",
                os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            try:
                child = subprocess.Popen(
                    [sys.executable, "-I", str(Path(__file__).resolve()), "--serve"],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                os.close(log)
            pending = ".pid-" + secrets.token_hex(12)
            try:
                identity = process_identity(child.pid)
                if identity is None:
                    raise RuntimeError("Session daemon exited before startup identity was recorded")
                fd = os.open(
                    pending,
                    os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=directory,
                )
                with os.fdopen(fd, "w") as output:
                    json.dump({"pid": child.pid, "start": identity}, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(pending, "daemon.pid", src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            except BaseException:
                # No request can have used this newly spawned daemon yet.
                # Never signal an existing PID read from the state file.
                try:
                    child.terminate()
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                raise
            finally:
                try:
                    os.unlink(pending, dir_fd=directory)
                except FileNotFoundError:
                    pass
            return {"pid": child.pid, "start": identity, "reused": False}
        finally:
            os.close(lock)
    finally:
        os.close(directory)


def main() -> None:
    if sys.argv[1:] == ["--serve"]:
        # All children load the immutable image bundle, not workspace/PYTHONPATH.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        os.environ.update(
            DECEPTICON_SKIP_BOOT="1",
            SANDBOX_ROOT_DIR=str(ROOT),
            SANDBOX_DAEMON_HOST="127.0.0.1",
            SANDBOX_DAEMON_PORT="9999",
            DECEPTICON_MANAGED_SESSION="1",
            HOME=str(ROOT),
            SHELL="/bin/bash",
        )
        import runpy

        runpy.run_module("decepticon.sandbox_server", run_name="__main__")
        return
    if sys.argv[1:]:
        raise ValueError("Unsupported managed startup operation")
    raw = sys.stdin.buffer.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Scope startup message too large")
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise ValueError("Scope startup message must be an object")
    print(json.dumps(bootstrap(message)))


if __name__ == "__main__":
    main()
