"""SandboxDaemonClient route-layer tests — transport-agnostic via FakeTransport.

These tests verify the daemon API route semantics (payloads, response
parsing, the local background-job mirror) WITHOUT any wire: the fake
implements the ``_request()`` seam with canned responses. The HTTP wire
itself has its own suite in ``test_http_transport.py``.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any

from decepticon.backends.daemon_client import (
    BackgroundJob,
    SandboxDaemonClient,
    _mirror_key,
)
from decepticon.backends.http_transport import HTTPTransportSandbox


class FakeDaemonTransport(SandboxDaemonClient):
    """Route-layer test double: canned daemon responses per route path."""

    def __init__(self, routes: dict[str, Any], *, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    @property
    def id(self) -> str:
        return "fake-daemon-transport"

    def _request(self, method: str, path: str, **kwargs: Any):
        self.calls.append((method, path, kwargs.get("json")))
        body = self.routes.get(path)
        if body is None:
            raise AssertionError(f"unexpected route: {method} {path}")
        return SimpleNamespace(json=lambda: body)


def test_transports_are_siblings_under_the_daemon_client() -> None:
    """The layer guarantee: transports share the route layer, not each other."""
    assert issubclass(HTTPTransportSandbox, SandboxDaemonClient)
    assert issubclass(FakeDaemonTransport, SandboxDaemonClient)
    assert not issubclass(HTTPTransportSandbox, FakeDaemonTransport)
    assert not issubclass(FakeDaemonTransport, HTTPTransportSandbox)


def test_execute_posts_command_and_parses_response() -> None:
    sb = FakeDaemonTransport({"/execute": {"output": "files", "exit_code": 0, "truncated": True}})
    result = sb.execute("ls", timeout=5)
    method, path, body = sb.calls[-1]
    assert method == "post" and path == "/execute"
    assert body == {"command": "ls", "timeout": 5}
    assert result.output == "files"
    assert result.exit_code == 0
    assert result.truncated is True


def test_execute_timeout_none_uses_default_and_get_defaults() -> None:
    sb = FakeDaemonTransport({"/execute": {"output": "root"}})
    result = sb.execute("whoami")
    assert sb.calls[-1][2]["timeout"] is None
    assert result.exit_code is None
    assert result.truncated is False


def test_upload_files_b64_encodes_payload() -> None:
    sb = FakeDaemonTransport(
        {
            "/upload_files": {
                "files": [{"path": "a.txt"}, {"path": "b.bin", "error": "permission_denied"}]
            }
        }
    )
    results = sb.upload_files([("a.txt", b"hello"), ("b.bin", b"\x00\x01")])
    _, path, body = sb.calls[-1]
    assert path == "/upload_files"
    entries = body["files"]
    assert entries[0]["data_b64"] == base64.b64encode(b"hello").decode("ascii")
    assert entries[1]["data_b64"] == base64.b64encode(b"\x00\x01").decode("ascii")
    assert results[0].path == "a.txt"
    assert results[0].error is None
    assert results[1].path == "b.bin"
    assert results[1].error == "permission_denied"


def test_download_files_decodes_b64_content() -> None:
    raw = b"data bytes"
    b64 = base64.b64encode(raw).decode("ascii")
    sb = FakeDaemonTransport(
        {
            "/download_files": {
                "files": [
                    {"path": "a.txt", "data_b64": b64},
                    {"path": "missing", "error": "file_not_found"},
                ]
            }
        }
    )
    results = sb.download_files(["a.txt", "missing"])
    assert results[0].content == raw
    assert results[0].error is None
    assert results[1].content is None
    assert results[1].error == "file_not_found"


def test_execute_tmux_posts_full_payload_and_returns_output() -> None:
    sb = FakeDaemonTransport({"/execute_tmux": {"output": "cpu..."}})
    result = sb.execute_tmux("top", session="scan", timeout=3, is_input=False, workspace_path="/ws")
    method, path, body = sb.calls[-1]
    assert method == "post" and path == "/execute_tmux"
    assert body == {
        "command": "top",
        "session": "scan",
        "timeout": 3,
        "is_input": False,
        "workspace_path": "/ws",
    }
    assert result == "cpu..."


def test_execute_tmux_default_timeout_none_branch() -> None:
    sb = FakeDaemonTransport({"/execute_tmux": {"output": "ok"}})
    assert sb.execute_tmux("ls") == "ok"


async def test_execute_tmux_async_runs_in_thread() -> None:
    sb = FakeDaemonTransport({"/execute_tmux": {"output": "done"}})
    out = await sb.execute_tmux_async("cmd", session="s", timeout=2, on_auto_background=None)
    assert out == "done"


async def test_execute_tmux_async_on_auto_background_not_called() -> None:
    called: list[bool] = []
    sb = FakeDaemonTransport({"/execute_tmux": {"output": "done"}})
    out = await sb.execute_tmux_async(
        "cmd", session="s", on_auto_background=lambda *a: called.append(True)
    )
    assert out == "done"
    assert called == []


def test_start_background_posts_and_registers_local_mirror() -> None:
    sb = FakeDaemonTransport({"/start_background": {}})
    sb.start_background("nmap t", session="scan", workspace_path="/ws")
    method, path, body = sb.calls[-1]
    assert method == "post" and path == "/start_background"
    assert body == {"command": "nmap t", "session": "scan", "workspace_path": "/ws"}
    job = sb._jobs.get(session="scan")
    assert job is not None
    assert job.command == "nmap t"
    assert job.status == "running"
    # The daemon receives the raw workspace_path, but the local mirror
    # normalizes anything outside the /workspace tree back to the default
    # (see _normalize_workspace), so an out-of-tree "/ws" is stored as
    # "/workspace" and the mirror key falls back to the bare session.
    assert job.workspace_path == "/workspace"


def test_start_background_defaults_workspace_path_to_workspace() -> None:
    sb = FakeDaemonTransport({"/start_background": {}})
    sb.start_background("cmd", session="s")
    job = sb._jobs.get(session="s")
    assert job is not None
    assert job.workspace_path == "/workspace"


def _job_dict(
    session: str = "scan",
    key: str = "k1",
    command: str = "c",
    initial_markers: int = 0,
    started_at: float = 1.0,
    workspace_path: str = "/ws",
    status: str = "done",
    exit_code: int | None = 0,
    completed_at: float | None = 2.0,
    consumed: bool = False,
) -> dict[str, Any]:
    return {
        "session": session,
        "key": key,
        "command": command,
        "initial_markers": initial_markers,
        "started_at": started_at,
        "workspace_path": workspace_path,
        "status": status,
        "exit_code": exit_code,
        "completed_at": completed_at,
        "consumed": consumed,
    }


def test_poll_completion_returns_none_when_job_null() -> None:
    sb = FakeDaemonTransport({"/poll_completion": {"job": None}})
    assert sb.poll_completion("scan") is None


def test_poll_completion_builds_job_and_marks_local_complete() -> None:
    jd = _job_dict(session="scan", status="done", exit_code=0)
    sb = FakeDaemonTransport({"/poll_completion": {"job": jd}})
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    result = sb.poll_completion("scan")
    assert result is not None
    assert isinstance(result, BackgroundJob)
    assert result.status == "done"
    assert result.exit_code == 0
    local = sb._jobs.get("scan")
    assert local is not None
    assert local.status == "done"


def test_poll_completion_reregisters_when_local_missing() -> None:
    jd = _job_dict(session="orphan", status="done", exit_code=0)
    sb = FakeDaemonTransport({"/poll_completion": {"job": jd}})
    result = sb.poll_completion("orphan")
    assert result is not None
    local = sb._jobs.get("orphan")
    assert local is not None
    assert local.status == "done"


def test_poll_completion_running_job_does_not_mark_complete() -> None:
    jd = _job_dict(session="scan", status="running", exit_code=None)
    sb = FakeDaemonTransport({"/poll_completion": {"job": jd}})
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    result = sb.poll_completion("scan")
    assert result is not None
    assert result.status == "running"
    local = sb._jobs.get("scan")
    assert local is not None
    assert local.status == "running"


def test_poll_completion_exit_code_none_falls_back_to_minus_one() -> None:
    jd = _job_dict(session="scan", status="done", exit_code=None)
    sb = FakeDaemonTransport({"/poll_completion": {"job": jd}})
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    sb.poll_completion("scan")
    local = sb._jobs.get("scan")
    assert local is not None
    assert local.exit_code == -1


def test_poll_completion_field_defaults_when_keys_missing() -> None:
    jd = {"session": "scan", "key": "k", "command": "c", "initial_markers": 0, "started_at": 1.0}
    sb = FakeDaemonTransport({"/poll_completion": {"job": jd}})
    result = sb.poll_completion("scan")
    assert result is not None
    assert result.workspace_path == "/workspace"
    assert result.status == "running"
    assert result.consumed is False


def test_kill_session_posts_correct_payload() -> None:
    sb = FakeDaemonTransport({"/kill_session": {}})
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    sb.kill_session("scan", workspace_path="/ws")
    method, path, body = sb.calls[-1]
    assert method == "post" and path == "/kill_session"
    assert body == {"session": "scan", "workspace_path": "/ws"}
    assert sb._jobs.get(session="scan") is None


def test_read_session_log_diff_returns_diff() -> None:
    sb = FakeDaemonTransport({"/read_session_log_diff": {"diff": "+ line"}})
    assert sb.read_session_log_diff("main", workspace_path="/ws") == "+ line"


def test_reset_session_log_offset_posts_and_returns_none() -> None:
    sb = FakeDaemonTransport({"/reset_session_log_offset": {}})
    sb.reset_session_log_offset("main", workspace_path="/ws")
    method, path, body = sb.calls[-1]
    assert method == "post" and path == "/reset_session_log_offset"
    assert body == {"session": "main", "workspace_path": "/ws"}


def test_session_log_path_returns_path() -> None:
    sb = FakeDaemonTransport({"/session_log_path": {"path": "/tmp/x.log"}})
    assert sb.session_log_path("main", workspace_path="/ws") == "/tmp/x.log"


def test_mirror_key_helpers_exported_for_transports() -> None:
    """The mirror-key helpers are shared by every transport implementation."""
    assert _mirror_key("scan", None) == "scan"
    # Out-of-tree paths normalize to the default → the bare session key.
    assert _mirror_key("scan", "/ws") == "scan"
    # In-tree paths get the workspace slug + digest prefix.
    assert _mirror_key("scan", "/workspace/proj").startswith("proj-")
    assert _mirror_key("scan", "/workspace/proj").endswith(":scan")
