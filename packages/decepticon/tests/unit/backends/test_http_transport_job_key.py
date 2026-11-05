import time as _t
from unittest.mock import MagicMock, patch

from decepticon.backends.http_transport import HTTPSandbox, _mirror_key


def _make_sandbox():
    sb = HTTPSandbox.__new__(HTTPSandbox)
    from decepticon.sandbox_kernel import BackgroundJobTracker

    sb._base_url = "http://localhost:9999"
    sb._token = None
    sb._timeout = 30.0
    sb._client = None
    sb._jobs = BackgroundJobTracker()
    return sb


def test_mirror_key_default_workspace_equals_session():
    assert _mirror_key("main", None) == "main"
    assert _mirror_key("main", "/workspace") == "main"


def test_mirror_key_non_default_workspace_is_composite():
    key = _mirror_key("main", "/workspace/eng-abc")
    assert key != "main"
    assert "main" in key
    assert ":" in key


def test_mirror_key_same_session_different_workspace_produces_different_keys():
    key_a = _mirror_key("main", "/workspace/eng-alpha")
    key_b = _mirror_key("main", "/workspace/eng-beta")
    assert key_a != key_b


def test_start_background_two_workspaces_same_session_no_collision():
    sb = _make_sandbox()

    with patch.object(sb, "_request"):
        sb.start_background("nmap a", session="main", workspace_path="/workspace/eng-alpha")
        sb.start_background("nmap b", session="main", workspace_path="/workspace/eng-beta")

    all_jobs = sb._jobs.all_jobs()
    assert len(all_jobs) == 2
    commands = {j.command for j in all_jobs}
    assert commands == {"nmap a", "nmap b"}


def test_start_background_second_workspace_does_not_overwrite_first():
    sb = _make_sandbox()

    with patch.object(sb, "_request"):
        sb.start_background("nmap a", session="main", workspace_path="/workspace/eng-alpha")
        sb.start_background("nmap b", session="main", workspace_path="/workspace/eng-beta")

    key_a = _mirror_key("main", "/workspace/eng-alpha")
    key_b = _mirror_key("main", "/workspace/eng-beta")
    job_a = sb._jobs.get(session="main", key=key_a)
    job_b = sb._jobs.get(session="main", key=key_b)
    assert job_a is not None and job_a.command == "nmap a"
    assert job_b is not None and job_b.command == "nmap b"


def _build_job_response(session, workspace, status="running", exit_code=None):
    return {
        "job": {
            "session": session,
            "key": _mirror_key(session, workspace),
            "command": "nmap x",
            "initial_markers": 1,
            "started_at": _t.monotonic(),
            "workspace_path": workspace or "/workspace",
            "status": status,
            "exit_code": exit_code,
            "completed_at": None,
            "consumed": False,
        }
    }


def test_poll_completion_updates_correct_workspace_job():
    sb = _make_sandbox()

    with patch.object(sb, "_request"):
        sb.start_background("nmap a", session="main", workspace_path="/workspace/eng-alpha")
        sb.start_background("nmap b", session="main", workspace_path="/workspace/eng-beta")

    mock_resp = MagicMock()
    mock_resp.json.return_value = _build_job_response(
        "main", "/workspace/eng-alpha", status="done", exit_code=0
    )

    with patch.object(sb, "_request", return_value=mock_resp):
        sb.poll_completion(session="main", workspace_path="/workspace/eng-alpha")

    key_a = _mirror_key("main", "/workspace/eng-alpha")
    key_b = _mirror_key("main", "/workspace/eng-beta")
    job_a = sb._jobs.get(session="main", key=key_a)
    job_b = sb._jobs.get(session="main", key=key_b)
    assert job_a is not None and job_a.status == "done"
    assert job_b is not None and job_b.status == "running"


def test_poll_completion_default_workspace_uses_session_key():
    sb = _make_sandbox()

    with patch.object(sb, "_request"):
        sb.start_background("nmap c", session="main", workspace_path=None)

    mock_resp = MagicMock()
    mock_resp.json.return_value = _build_job_response(
        "main", "/workspace", status="done", exit_code=0
    )

    with patch.object(sb, "_request", return_value=mock_resp):
        sb.poll_completion(session="main", workspace_path=None)

    job = sb._jobs.get(session="main", key="main")
    assert job is not None and job.status == "done"
