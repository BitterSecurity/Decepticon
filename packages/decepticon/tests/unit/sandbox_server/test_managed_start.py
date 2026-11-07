from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("fcntl")


@pytest.fixture
def managed():
    return importlib.import_module("decepticon.sandbox_server.managed_start")


@pytest.fixture
def prepared(tmp_path, monkeypatch, managed):
    root = tmp_path / "workspace"
    root.mkdir()
    child = Mock(pid=987654)
    spawn = Mock(return_value=child)
    monkeypatch.setattr(managed.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        managed, "process_identity", lambda pid: "owned-start" if pid == child.pid else None
    )
    content = b'{"machine_enforcement":{"in_scope":["example.com"]}}'
    message = {
        "roe_b64": base64.b64encode(content).decode(),
        "roe_sha256": hashlib.sha256(content).hexdigest(),
    }
    return root, tmp_path / "state", content, message, child, spawn


@pytest.mark.parametrize("scope_umask", [0o022, 0o077])
def test_scope_bytes_restored_without_exposing_a_configurable_path(managed, prepared, scope_umask):
    root, state, content, message, child, spawn = prepared
    previous = os.umask(scope_umask)
    try:
        result = managed.bootstrap(message, root=root, state=state)
    finally:
        os.umask(previous)
    assert result == {"pid": child.pid, "start": "owned-start", "reused": False}
    assert (root / "plan/roe.json").read_bytes() == content
    mode = (root / "plan/roe.json").stat().st_mode
    assert mode & 0o400
    assert not mode & 0o222
    assert not mode & 0o077, "Approved scope must be private to the admitted workload UID"
    args, kwargs = spawn.call_args
    assert args[0][1] == "-I" and args[0][-1] == "--serve"
    assert kwargs["cwd"] == root


def test_reconnect_reuses_same_process_and_does_not_truncate_log(managed, prepared):
    root, state, content, message, child, spawn = prepared
    managed.bootstrap(message, root=root, state=state)
    (state / "daemon.log").write_bytes(b"retained log\n")
    assert managed.bootstrap(message, root=root, state=state) == {
        "pid": child.pid,
        "start": "owned-start",
        "reused": True,
    }
    assert spawn.call_count == 1
    assert (state / "daemon.log").read_bytes() == b"retained log\n"


def test_concurrent_reconnects_start_one_process(managed, prepared):
    root, state, content, message, child, spawn = prepared
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: managed.bootstrap(message, root=root, state=state), range(8))
        )
    assert spawn.call_count == 1
    assert sum(not row["reused"] for row in results) == 1
    assert {row["pid"] for row in results} == {child.pid}


@pytest.mark.parametrize("change", ["hash", "extra", "not_json", "not_object"])
def test_invalid_scope_never_spawns_or_writes_authority(managed, prepared, change):
    root, state, content, message, child, spawn = prepared
    if change == "hash":
        message["roe_sha256"] = "0" * 64
    elif change == "extra":
        message["root"] = "/another-workspace"
    else:
        content = b"not-json" if change == "not_json" else b"[]"
        message = {
            "roe_b64": base64.b64encode(content).decode(),
            "roe_sha256": hashlib.sha256(content).hexdigest(),
        }
    with pytest.raises(ValueError):
        managed.bootstrap(message, root=root, state=state)
    spawn.assert_not_called()
    assert not (root / "plan").exists()


def test_plan_symlink_cannot_redirect_scope_write(managed, prepared, tmp_path):
    root, state, content, message, child, spawn = prepared
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "roe.json").write_bytes(b"preserve")
    (root / "plan").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        managed.bootstrap(message, root=root, state=state)
    assert (outside / "roe.json").read_bytes() == b"preserve"
    spawn.assert_not_called()


@pytest.mark.parametrize("name", ["startup.lock", "daemon.log", "daemon.pid"])
def test_state_symlinks_are_not_followed(managed, prepared, tmp_path, name):
    root, state, content, message, child, spawn = prepared
    state.mkdir()
    outside = tmp_path / "unrelated"
    outside.write_text("preserve")
    (state / name).symlink_to(outside)
    with pytest.raises(OSError):
        managed.bootstrap(message, root=root, state=state)
    assert outside.read_text() == "preserve"
    spawn.assert_not_called()


def test_stale_pid_does_not_adopt_an_unrelated_process(managed, prepared):
    root, state, content, message, child, spawn = prepared
    state.mkdir()
    (state / "daemon.pid").write_text(json.dumps({"pid": 12345, "start": "old"}))
    assert managed.bootstrap(message, root=root, state=state)["reused"] is False
    assert spawn.call_count == 1


def test_real_proc_identity_is_observable_without_argv_or_environment(managed):
    assert managed.process_identity(os.getpid())
    assert managed.process_identity(2147483647) is None


def test_pid_record_failure_cleans_up_only_the_new_unadmitted_child(managed, prepared, monkeypatch):
    root, state, content, message, child, spawn = prepared
    original = managed.os.fsync
    calls = 0

    def fsync(fd):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("owned fixture storage failure")
        return original(fd)

    monkeypatch.setattr(managed.os, "fsync", fsync)
    with pytest.raises(OSError, match="fixture storage"):
        managed.bootstrap(message, root=root, state=state)
    child.terminate.assert_called_once()
    child.wait.assert_called_once()


def test_managed_serve_pins_shell_home_and_immutable_package_root(managed, monkeypatch):
    import runpy
    from pathlib import Path

    monkeypatch.setattr(managed.sys, "argv", ["managed_start.py", "--serve"])
    monkeypatch.setattr(managed.sys, "path", list(managed.sys.path))

    def start(module, run_name):
        assert module == "decepticon.sandbox_server" and run_name == "__main__"
        assert os.environ["HOME"] == "/workspace"
        assert os.environ["SHELL"] == "/bin/bash"
        assert os.environ["SANDBOX_DAEMON_HOST"] == "127.0.0.1"
        assert os.environ["SANDBOX_DAEMON_PORT"] == "9999"
        assert os.environ["DECEPTICON_MANAGED_SESSION"] == "1"
        assert managed.sys.path[0] == str(Path(managed.__file__).resolve().parents[2])

    monkeypatch.setattr(runpy, "run_module", start)
    with patch.dict(os.environ, {"HOME": "/wrong", "SHELL": "/bin/sh"}):
        managed.main()


def test_managed_health_reports_real_process_identity(managed):
    from decepticon.sandbox_server.app import healthz

    with patch.dict(os.environ, {"DECEPTICON_MANAGED_SESSION": "1"}):
        assert healthz() == {
            "status": "ok",
            "pid": os.getpid(),
            "start": managed.process_identity(os.getpid()),
        }
