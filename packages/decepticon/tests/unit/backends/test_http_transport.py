from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from decepticon.backends.http_transport import HTTPSandbox, SandboxError, _retry_on_connection_error
from decepticon.sandbox_kernel import BackgroundJob


def _make_handler(status: int = 200, body: dict[str, Any] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {})

    return handler


def _inject_client(sb: HTTPSandbox, handler: Any) -> None:
    sb._client = httpx.Client(
        base_url=sb._base_url,
        transport=httpx.MockTransport(handler),
    )


def test_id_property_strips_trailing_slash() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999/")
    assert sb.id == "http-sandbox:http://localhost:9999"


def test_id_property_no_trailing_slash() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    assert sb.id == "http-sandbox:http://localhost:9999"


def test_http_lazy_client_created_once_and_cached() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    c1 = sb._http()
    c2 = sb._http()
    assert c1 is c2
    assert sb._client is not None
    assert "decepticon-http-sandbox/1" in c1.headers["user-agent"]
    assert "authorization" not in c1.headers


def test_http_sets_bearer_header_when_token_provided() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999", token="secret")
    client = sb._http()
    assert client.headers["authorization"] == "Bearer secret"


def test_http_no_authorization_header_when_token_is_none() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999", token=None)
    client = sb._http()
    assert "authorization" not in client.headers


def test_close_idempotent_first_call_clears_client() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    sb._http()
    assert sb._client is not None
    sb.close()
    assert sb._client is None


def test_close_idempotent_second_call_does_not_raise() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    sb._http()
    sb.close()
    sb.close()
    assert sb._client is None


def test_close_when_no_client_initialized_does_not_raise() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    assert sb._client is None
    sb.close()


def test_retry_succeeds_first_try_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    result = _retry_on_connection_error(lambda: "ok")
    assert result == "ok"
    assert sleep_calls == []


def test_retry_recovers_after_two_transient_connect_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    sentinel = object()
    attempts: list[int] = []

    def flaky() -> object:
        attempts.append(1)
        if len(attempts) <= 2:
            raise httpx.ConnectError("refused")
        return sentinel

    result = _retry_on_connection_error(flaky)
    assert result is sentinel
    assert sleep_calls == [0.5, 1.0]


def test_retry_exhausts_reraises_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("decepticon.backends.http_transport.time.sleep", lambda d: None)

    def always_fail() -> None:
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        _retry_on_connection_error(always_fail)


def test_retry_rides_out_sandbox_boot_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-engagement sandbox can refuse connections for 1-2 min while it
    boots. The retry budget must outlast that window — far more than the old
    3-attempt cap — so the agent's first tool call survives the boot."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    sentinel = object()
    attempts: list[int] = []

    def booting() -> object:
        attempts.append(1)
        # Refuse for the first 15 attempts (would blow the old max_retries=3).
        if len(attempts) <= 15:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        return sentinel

    result = _retry_on_connection_error(booting)
    assert result is sentinel
    assert len(attempts) == 16
    # Backoff is capped (no unbounded growth) so the loop keeps probing.
    assert max(sleep_calls) <= 8.0


def test_retry_budget_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely dead sandbox must still surface within the time budget —
    the cumulative (intended) backoff stays within max_total_delay."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )

    def always_fail() -> None:
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        _retry_on_connection_error(always_fail, max_total_delay=30.0, max_delay=8.0)
    assert sum(sleep_calls) <= 30.0
    assert max(sleep_calls) <= 8.0


def test_request_wraps_http_status_error_as_sandbox_error() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, _make_handler(500, None))
    with pytest.raises(SandboxError) as exc_info:
        sb._request("post", "/x", json={})
    assert "500" in str(exc_info.value)


def test_request_long_body_truncated_in_sandbox_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="e" * 500)

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    with pytest.raises(SandboxError) as exc_info:
        sb._request("post", "/x", json={})
    msg = str(exc_info.value)
    assert "503" in msg
    assert len(msg) < 1000


def test_request_success_returns_response() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, _make_handler(200, {"ok": 1}))
    resp = sb._request("get", "/y")
    assert resp.status_code == 200
    assert resp.json() == {"ok": 1}


def test_execute_posts_command_and_parses_response() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": "files", "exit_code": 0, "truncated": True})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.execute("ls", timeout=5)
    assert captured["path"] == "/execute"
    assert captured["body"] == {"command": "ls", "timeout": 5}
    assert result.output == "files"
    assert result.exit_code == 0
    assert result.truncated is True


def test_execute_timeout_none_uses_default_and_get_defaults() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": "root"})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.execute("whoami")
    assert captured["body"]["timeout"] is None
    assert result.exit_code is None
    assert result.truncated is False


def test_upload_files_b64_encodes_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"files": [{"path": "a.txt"}, {"path": "b.bin", "error": "permission_denied"}]},
        )

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    results = sb.upload_files([("a.txt", b"hello"), ("b.bin", b"\x00\x01")])
    assert captured["path"] == "/upload_files"
    entries = captured["body"]["files"]
    assert entries[0]["data_b64"] == base64.b64encode(b"hello").decode("ascii")
    assert entries[1]["data_b64"] == base64.b64encode(b"\x00\x01").decode("ascii")
    assert results[0].path == "a.txt"
    assert results[0].error is None
    assert results[1].path == "b.bin"
    assert results[1].error == "permission_denied"


def test_download_files_decodes_b64_content() -> None:
    raw = b"data bytes"
    b64 = base64.b64encode(raw).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "files": [
                    {"path": "a.txt", "data_b64": b64},
                    {"path": "missing", "error": "file_not_found"},
                ]
            },
        )

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    results = sb.download_files(["a.txt", "missing"])
    assert results[0].content == raw
    assert results[0].error is None
    assert results[1].content is None
    assert results[1].error == "file_not_found"


def test_execute_tmux_posts_full_payload_and_returns_output() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": "cpu..."})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.execute_tmux("top", session="scan", timeout=3, is_input=False, workspace_path="/ws")
    assert captured["path"] == "/execute_tmux"
    b = captured["body"]
    assert b["command"] == "top"
    assert b["session"] == "scan"
    assert b["timeout"] == 3
    assert b["is_input"] is False
    assert b["workspace_path"] == "/ws"
    assert result == "cpu..."


def test_execute_tmux_default_timeout_none_branch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "ok"})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.execute_tmux("ls")
    assert result == "ok"


async def test_execute_tmux_async_runs_in_thread() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "done"})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    out = await sb.execute_tmux_async("cmd", session="s", timeout=2, on_auto_background=None)
    assert out == "done"


async def test_execute_tmux_async_on_auto_background_not_called() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": "done"})

    called: list[bool] = []
    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    out = await sb.execute_tmux_async(
        "cmd", session="s", on_auto_background=lambda *a: called.append(True)
    )
    assert out == "done"
    assert called == []


def test_start_background_posts_and_registers_local_mirror() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    sb.start_background("nmap t", session="scan", workspace_path="/ws")
    assert captured["path"] == "/start_background"
    assert captured["body"]["command"] == "nmap t"
    assert captured["body"]["session"] == "scan"
    assert captured["body"]["workspace_path"] == "/ws"
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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
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
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": None})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    assert sb.poll_completion("scan") is None


def test_poll_completion_builds_job_and_marks_local_complete() -> None:
    jd = _job_dict(session="scan", status="done", exit_code=0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": jd})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": jd})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    assert sb._jobs.get("orphan") is None
    result = sb.poll_completion("orphan")
    assert result is not None
    assert sb._jobs.get("orphan") is not None


def test_poll_completion_running_job_does_not_mark_complete() -> None:
    jd = _job_dict(session="scan", status="running", exit_code=None, completed_at=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": jd})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    sb.poll_completion("scan")
    local = sb._jobs.get("scan")
    assert local is not None
    assert local.status == "running"


def test_poll_completion_exit_code_none_falls_back_to_minus_one() -> None:
    jd = _job_dict(session="scan", status="done", exit_code=None)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": jd})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    sb._jobs.register(session="scan", command="c", initial_markers=0)
    sb.poll_completion("scan")
    local = sb._jobs.get("scan")
    assert local is not None
    assert local.exit_code == -1


def test_poll_completion_field_defaults_when_keys_missing() -> None:
    minimal: dict[str, Any] = {
        "session": "scan",
        "key": "k",
        "command": "c",
        "initial_markers": 0,
        "started_at": 1.0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"job": minimal})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.poll_completion("scan")
    assert result is not None
    assert result.workspace_path == "/workspace"
    assert result.status == "running"
    assert result.exit_code is None
    assert result.consumed is False


def test_kill_session_posts_correct_payload() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    sb.kill_session("scan", workspace_path="/ws")
    assert captured["path"] == "/kill_session"
    assert captured["body"] == {"session": "scan", "workspace_path": "/ws"}


def test_read_session_log_diff_returns_diff() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"diff": "+added line"})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    assert sb.read_session_log_diff("scan") == "+added line"


def test_reset_session_log_offset_posts_and_returns_none() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    result = sb.reset_session_log_offset("scan")
    assert result is None
    assert captured["path"] == "/reset_session_log_offset"


def test_session_log_path_returns_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"path": "/workspace/.sessions/scan.log"})

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    assert sb.session_log_path("scan") == "/workspace/.sessions/scan.log"
