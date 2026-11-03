"""Tests for the sandbox HTTP daemon FastAPI app.

The daemon (``decepticon/sandbox_server/app.py``) is a thin transport
layer over a ``DaemonSandbox``. These tests exercise the wire surface
with a mocked backend so no real subprocess / tmux / filesystem IO
happens — every backend method is stubbed and only the request/response
marshalling plus the bearer-token auth model are under test.

Two module globals (``_backend``, ``_required_token``) persist for the
process lifetime; the autouse fixture resets them around every test so
ordering can't leak state.
"""

from __future__ import annotations

import base64
import contextlib
import importlib
import os
import types
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Import the module object explicitly. ``decepticon.sandbox_server.__init__``
# binds ``app`` (the FastAPI instance) as a package attribute, so a plain
# ``import decepticon.sandbox_server.app as app_module`` would resolve
# ``.app`` to that instance rather than the submodule. ``import_module``
# always returns the module.
app_module = importlib.import_module("decepticon.sandbox_server.app")
app = app_module.app

TOKEN = "s3cr3t-token"  # noqa: S105  — test fixture, not a real credential


@pytest.fixture(autouse=True)
def _reset_module_globals() -> Iterator[None]:
    """Reset the daemon's module-level singletons before and after each test.

    ``_backend`` and ``_required_token`` live for the daemon's lifetime in
    production; in the test process they would otherwise leak across tests.
    ``setattr`` (rather than direct attribute assignment) keeps the mutation
    type-clean against the generic ``ModuleType`` of an imported module.
    """
    for name in ("_backend", "_required_token"):
        setattr(app_module, name, None)
    yield
    for name in ("_backend", "_required_token"):
        setattr(app_module, name, None)


def _make_backend() -> MagicMock:
    """A mock backend whose method return values use ``SimpleNamespace`` so
    the attributes feed cleanly into the pydantic response models."""
    backend = MagicMock()
    backend.execute.return_value = types.SimpleNamespace(
        output="hello\n", exit_code=0, truncated=False
    )
    backend.upload_files.return_value = [
        types.SimpleNamespace(path="/workspace/a.txt", error=None),
    ]
    backend.download_files.return_value = [
        types.SimpleNamespace(path="/workspace/a.txt", content=b"file-bytes", error=None),
    ]
    backend.execute_tmux.return_value = "tmux-output"
    backend.start_background.return_value = None
    backend.poll_completion.return_value = None
    backend.kill_session.return_value = None
    backend.read_session_log_diff.return_value = "log-diff-text"
    backend.reset_session_log_offset.return_value = None
    backend.session_log_path.return_value = "/workspace/.sessions/main.log"
    return backend


@contextlib.contextmanager
def _client(backend: MagicMock, *, token: str | None = None) -> Iterator[TestClient]:
    """Yield a TestClient with ``_get_backend`` patched to return ``backend``.

    ``token`` controls the ``SANDBOX_TOKEN`` env var that ``lifespan``
    reads into ``_required_token``. The env mutation and ``_get_backend``
    patch are both in place before the ``TestClient`` context is entered, so
    ``lifespan`` (which runs on enter) sees the stub backend and the
    intended token state. ``token=None`` actively removes any inherited
    ``SANDBOX_TOKEN`` so the no-auth path is deterministic.
    """
    env: dict[str, str] = {} if token is None else {"SANDBOX_TOKEN": token}
    saved = os.environ.get("SANDBOX_TOKEN")
    with patch.object(app_module, "_get_backend", return_value=backend):
        os.environ.pop("SANDBOX_TOKEN", None)
        os.environ.update(env)
        try:
            with TestClient(app) as client:
                yield client
        finally:
            os.environ.pop("SANDBOX_TOKEN", None)
            if saved is not None:
                os.environ["SANDBOX_TOKEN"] = saved


# ── /healthz ─────────────────────────────────────────────────────────────


def test_healthz_returns_ok_without_touching_backend() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # Liveness must not poke any sandbox method.
    backend.execute.assert_not_called()
    backend.poll_completion.assert_not_called()


# ── /execute ─────────────────────────────────────────────────────────────


def test_execute_returns_backend_result() -> None:
    backend = _make_backend()
    backend.execute.return_value = types.SimpleNamespace(
        output="result-out", exit_code=7, truncated=True
    )
    with _client(backend) as client:
        resp = client.post("/execute", json={"command": "ls -la", "timeout": 30})
    assert resp.status_code == 200
    assert resp.json() == {"output": "result-out", "exit_code": 7, "truncated": True}
    backend.execute.assert_called_once_with("ls -la", timeout=30)


def test_execute_timeout_defaults_to_none() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post("/execute", json={"command": "echo hi"})
    assert resp.status_code == 200
    backend.execute.assert_called_once_with("echo hi", timeout=None)


def test_execute_missing_command_is_422() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post("/execute", json={"timeout": 5})
    assert resp.status_code == 422


# ── /upload_files ────────────────────────────────────────────────────────


def test_upload_files_base64_decodes_payload() -> None:
    backend = _make_backend()
    content = b"line1\nline2\n"
    data_b64 = base64.b64encode(content).decode("ascii")
    with _client(backend) as client:
        resp = client.post(
            "/upload_files",
            json={"files": [{"path": "/workspace/a.txt", "data_b64": data_b64}]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"files": [{"path": "/workspace/a.txt", "error": None}]}
    # The route must hand the backend decoded bytes, not the base64 string.
    backend.upload_files.assert_called_once_with([("/workspace/a.txt", content)])


def test_upload_files_propagates_per_file_error() -> None:
    backend = _make_backend()
    backend.upload_files.return_value = [
        types.SimpleNamespace(path="/bad", error="permission_denied"),
    ]
    data_b64 = base64.b64encode(b"x").decode("ascii")
    with _client(backend) as client:
        resp = client.post(
            "/upload_files",
            json={"files": [{"path": "/bad", "data_b64": data_b64}]},
        )
    assert resp.status_code == 200
    assert resp.json() == {"files": [{"path": "/bad", "error": "permission_denied"}]}


# ── /download_files ──────────────────────────────────────────────────────


def test_download_files_base64_encodes_content() -> None:
    backend = _make_backend()
    backend.download_files.return_value = [
        types.SimpleNamespace(path="/workspace/a.txt", content=b"abc123", error=None),
    ]
    with _client(backend) as client:
        resp = client.post("/download_files", json={"paths": ["/workspace/a.txt"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["files"][0]["path"] == "/workspace/a.txt"
    assert body["files"][0]["error"] is None
    assert base64.b64decode(body["files"][0]["data_b64"]) == b"abc123"
    backend.download_files.assert_called_once_with(["/workspace/a.txt"])


def test_download_files_none_content_yields_null_data_b64() -> None:
    backend = _make_backend()
    backend.download_files.return_value = [
        types.SimpleNamespace(path="/missing", content=None, error="file_not_found"),
    ]
    with _client(backend) as client:
        resp = client.post("/download_files", json={"paths": ["/missing"]})
    assert resp.status_code == 200
    assert resp.json() == {
        "files": [{"path": "/missing", "data_b64": None, "error": "file_not_found"}],
    }


# ── /execute_tmux ────────────────────────────────────────────────────────


def test_execute_tmux_forwards_all_fields() -> None:
    backend = _make_backend()
    backend.execute_tmux.return_value = "screen-capture"
    with _client(backend) as client:
        resp = client.post(
            "/execute_tmux",
            json={
                "command": "top",
                "session": "scan",
                "timeout": 15,
                "is_input": True,
                "workspace_path": "/workspace/eng",
            },
        )
    assert resp.status_code == 200
    assert resp.json() == {"output": "screen-capture"}
    backend.execute_tmux.assert_called_once_with(
        command="top",
        session="scan",
        timeout=15,
        is_input=True,
        workspace_path="/workspace/eng",
    )


def test_execute_tmux_uses_defaults_for_omitted_fields() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post("/execute_tmux", json={})
    assert resp.status_code == 200
    backend.execute_tmux.assert_called_once_with(
        command="",
        session="main",
        timeout=None,
        is_input=False,
        workspace_path=None,
    )


# ── /start_background, /kill_session, /reset_session_log_offset ───────────


def test_start_background_returns_status_ok() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post(
            "/start_background",
            json={"command": "sleep 100", "session": "bg", "workspace_path": "/workspace/eng"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    backend.start_background.assert_called_once_with(
        command="sleep 100",
        session="bg",
        workspace_path="/workspace/eng",
    )


def test_kill_session_returns_status_ok() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post("/kill_session", json={"session": "bg"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    backend.kill_session.assert_called_once_with(session="bg", workspace_path=None)


def test_reset_session_log_offset_returns_status_ok() -> None:
    backend = _make_backend()
    with _client(backend) as client:
        resp = client.post("/reset_session_log_offset", json={"session": "bg"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    backend.reset_session_log_offset.assert_called_once_with(session="bg", workspace_path=None)


# ── /poll_completion ─────────────────────────────────────────────────────


def test_poll_completion_none_job_yields_null() -> None:
    backend = _make_backend()
    backend.poll_completion.return_value = None
    with _client(backend) as client:
        resp = client.post("/poll_completion", json={"session": "bg"})
    assert resp.status_code == 200
    assert resp.json() == {"job": None}


def test_poll_completion_populated_job_copies_every_field() -> None:
    backend = _make_backend()
    job = types.SimpleNamespace(
        session="bg",
        key="bg::/workspace",
        command="sleep 100",
        initial_markers=3,
        started_at=111.5,
        workspace_path="/workspace/eng",
        status="completed",
        exit_code=0,
        completed_at=222.5,
        consumed=True,
    )
    backend.poll_completion.return_value = job
    with _client(backend) as client:
        resp = client.post("/poll_completion", json={"session": "bg"})
    assert resp.status_code == 200
    assert resp.json() == {
        "job": {
            "session": "bg",
            "key": "bg::/workspace",
            "command": "sleep 100",
            "initial_markers": 3,
            "started_at": 111.5,
            "workspace_path": "/workspace/eng",
            "status": "completed",
            "exit_code": 0,
            "completed_at": 222.5,
            "consumed": True,
        },
    }


# ── /read_session_log_diff, /session_log_path ────────────────────────────


def test_read_session_log_diff_returns_diff() -> None:
    backend = _make_backend()
    backend.read_session_log_diff.return_value = "new log bytes"
    with _client(backend) as client:
        resp = client.post(
            "/read_session_log_diff",
            json={"session": "scan", "workspace_path": "/workspace/eng"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"diff": "new log bytes"}
    backend.read_session_log_diff.assert_called_once_with(
        session="scan", workspace_path="/workspace/eng"
    )


def test_session_log_path_returns_path() -> None:
    backend = _make_backend()
    backend.session_log_path.return_value = "/workspace/eng/.sessions/scan.log"
    with _client(backend) as client:
        resp = client.post("/session_log_path", json={"session": "scan"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "/workspace/eng/.sessions/scan.log"}
    backend.session_log_path.assert_called_once_with(session="scan", workspace_path=None)


# ── Auth model ───────────────────────────────────────────────────────────


def test_no_token_env_allows_unauthenticated_request() -> None:
    """With SANDBOX_TOKEN unset, any caller (no header) is served."""
    backend = _make_backend()
    with _client(backend, token=None) as client:
        resp = client.post("/execute", json={"command": "echo hi"})
    assert resp.status_code == 200


def test_token_set_missing_header_is_401() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post("/execute", json={"command": "echo hi"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing bearer token"


def test_token_set_wrong_scheme_is_401() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post(
            "/execute",
            json={"command": "echo hi"},
            headers={"Authorization": f"Basic {TOKEN}"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing bearer token"


def test_token_set_wrong_token_is_401() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post(
            "/execute",
            json={"command": "echo hi"},
            headers={"Authorization": "Bearer not-the-token"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid token"


def test_token_set_correct_bearer_is_200() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post(
            "/execute",
            json={"command": "echo hi"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 200
    assert resp.json()["output"] == "hello\n"


# ── Shutdown cleanup ─────────────────────────────────────────────────────
#
# Zombie reaping is delegated to the container init process (``init: true``
# on the sandbox compose service), NOT a process-wide SIGCHLD handler — the
# latter races with ``subprocess.run`` and clobbers exit codes to 0. So
# there is no reaper to assert on at startup; only the tmux-session teardown.


def test_lifespan_shutdown_kills_all_tmux_sessions() -> None:
    """The shutdown hook must drain every tmux session via kill_all_sessions
    so tmux servers do not outlive the daemon and leak zombies."""
    backend = _make_backend()
    backend.kill_all_sessions.return_value = 3
    with _client(backend) as _client_ctx:
        pass  # context exit triggers lifespan shutdown
    backend.kill_all_sessions.assert_called_once()


def test_lifespan_shutdown_swallows_kill_all_sessions_errors() -> None:
    """A failure tearing down tmux must not crash the daemon shutdown."""
    backend = _make_backend()
    backend.kill_all_sessions.side_effect = RuntimeError("tmux server dead")
    # If the lifespan re-raises, _client's __exit__ would propagate.
    with _client(backend) as _client_ctx:
        pass


