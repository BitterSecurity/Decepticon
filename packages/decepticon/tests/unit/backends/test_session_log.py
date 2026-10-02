"""Pipe-pane logs are engagement-scoped and manager dict is lock-protected."""

import logging
import subprocess as _sp
import threading
from subprocess import CalledProcessError
from unittest.mock import patch

from deepagents.backends.protocol import FileDownloadResponse

from decepticon.sandbox_kernel import BackgroundJobTracker, TmuxSessionManager
from decepticon.sandbox_kernel.daemon import DaemonSandbox


def test_initialize_does_not_create_root_workspace_sessions_log():
    mgr = TmuxSessionManager("scan-1", "decepticon-sandbox")
    TmuxSessionManager._initialized.discard("scan-1")

    with (
        patch.object(mgr, "_sync_passthrough_env"),
        patch.object(mgr, "_tmux") as mock_tmux,
        patch("decepticon.sandbox_kernel.base.subprocess.run") as mock_run,
        patch("time.sleep"),
    ):
        mock_tmux.side_effect = [
            RuntimeError("session not found"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",  # _sync_passthrough_env -> _send -> send-keys -l
            "",  # _sync_passthrough_env -> _send -> send-keys Enter
        ]
        mock_run.return_value.returncode = 0
        mgr.initialize()

    assert not any(c.args[0][0] == "pipe-pane" for c in mock_tmux.call_args_list)
    assert not any("mkdir" in (c.args[0] if c.args else []) for c in mock_run.call_args_list)


def test_initialize_pipes_pane_to_engagement_scoped_sessions_log():
    mgr = TmuxSessionManager(
        "dcptn_test-main",
        "decepticon-sandbox",
        workspace_path="/workspace/test",
        log_name="main",
    )
    TmuxSessionManager._initialized.discard("dcptn_test-main")

    with (
        patch.object(mgr, "_sync_passthrough_env"),
        patch.object(mgr, "_tmux") as mock_tmux,
        patch("decepticon.sandbox_kernel.base.subprocess.run") as mock_run,
        patch("time.sleep"),
    ):
        mock_tmux.side_effect = [
            RuntimeError("session not found"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",  # _sync_passthrough_env -> _send -> send-keys -l
            "",  # _sync_passthrough_env -> _send -> send-keys Enter
        ]
        mock_run.return_value.returncode = 0
        mgr.initialize()

    new_session_call = next(c for c in mock_tmux.call_args_list if c.args[0][0] == "new-session")
    new_session_args = new_session_call.args[0]
    assert new_session_args[new_session_args.index("-c") + 1] == "/workspace/test"

    pipe_pane_call = next(c for c in mock_tmux.call_args_list if c.args[0][0] == "pipe-pane")
    pipe_pane_args = pipe_pane_call.args[0]
    cmd_arg = pipe_pane_args[pipe_pane_args.index("-o") + 1]
    assert cmd_arg == "cat >> /workspace/test/.sessions/main.log"


def test_initialize_creates_sessions_directory_inside_engagement_workspace():
    mgr = TmuxSessionManager(
        "dcptn_test-scan-2",
        "decepticon-sandbox",
        workspace_path="/workspace/test",
        log_name="scan-2",
    )
    TmuxSessionManager._initialized.discard("dcptn_test-scan-2")

    with (
        patch.object(mgr, "_sync_passthrough_env"),
        patch.object(mgr, "_tmux") as mock_tmux,
        patch("decepticon.sandbox_kernel.base.subprocess.run") as mock_run,
        patch("time.sleep"),
    ):
        mock_tmux.side_effect = [
            RuntimeError("session not found"),
            "",
            "",
            "",
            "",
            "",
            "",
            "",  # _sync_passthrough_env -> _send -> send-keys -l
            "",  # _sync_passthrough_env -> _send -> send-keys Enter
        ]
        mock_run.return_value.returncode = 0
        mgr.initialize()

    mkdir_calls = [
        c
        for c in mock_run.call_args_list
        if "mkdir" in (c.args[0] if c.args else [])
        and "/workspace/test/.sessions" in (c.args[0] if c.args else [])
    ]
    assert mkdir_calls, "Expected a docker exec mkdir call"
    cmd = mkdir_calls[0].args[0]
    assert "/workspace/test/.sessions" in cmd


def test_initialize_warns_when_mkdir_fails(caplog):
    mgr = TmuxSessionManager(
        "dcptn_test-scan-3",
        "decepticon-sandbox",
        workspace_path="/workspace/test",
        log_name="scan-3",
    )
    TmuxSessionManager._initialized.discard("dcptn_test-scan-3")

    decepticon_logger = logging.getLogger("decepticon")
    original_propagate = decepticon_logger.propagate
    decepticon_logger.propagate = True
    try:
        with (
            patch.object(mgr, "_sync_passthrough_env"),
            patch.object(mgr, "_tmux") as mock_tmux,
            patch("decepticon.sandbox_kernel.base.subprocess.run") as mock_run,
            patch("time.sleep"),
        ):
            mock_tmux.side_effect = [
                RuntimeError("session not found"),
                "",
                "",
                "",
                "",
                "",
                "",
                "",  # _sync_passthrough_env -> _send -> send-keys -l
                "",  # _sync_passthrough_env -> _send -> send-keys Enter
            ]
            mock_run.side_effect = [None, CalledProcessError(1, ["docker", "exec"])]
            with caplog.at_level(logging.WARNING):
                mgr.initialize()
    finally:
        decepticon_logger.propagate = original_propagate

    assert any("pipe-pane setup failed" in r.message for r in caplog.records), (
        f"Expected warning log; got {[r.message for r in caplog.records]}"
    )


def test_get_manager_concurrent_returns_same_instance():
    sandbox = DaemonSandbox(container_name="test")
    seen: list[int] = []
    seen_lock = threading.Lock()

    def worker():
        mgr = sandbox._get_manager("shared")
        with seen_lock:
            seen.append(id(mgr))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(seen)) == 1, "All threads must see the same manager instance"


def test_sandbox_has_jobs_tracker():
    sandbox = DaemonSandbox(container_name="test")
    assert isinstance(sandbox._jobs, BackgroundJobTracker)


def test_sandbox_has_log_offsets_dict():
    sandbox = DaemonSandbox(container_name="test")
    assert isinstance(sandbox._log_offsets, dict)
    assert sandbox._log_offsets == {}


def _file_response(path: str, content: bytes) -> FileDownloadResponse:
    return FileDownloadResponse(path=path, content=content, error=None)


def test_read_session_log_diff_returns_full_log_on_first_call():
    sandbox = DaemonSandbox(container_name="test")

    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [
            _file_response("/workspace/.sessions/scan.log", b"line1\nline2\nline3\n"),
        ]
        diff = sandbox.read_session_log_diff("scan")

    assert "line1" in diff and "line3" in diff


def test_read_session_log_diff_uses_engagement_workspace_path():
    sandbox = DaemonSandbox(container_name="test")

    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [
            _file_response("/workspace/test/.sessions/scan.log", b"scoped\n"),
        ]
        diff = sandbox.read_session_log_diff("scan", workspace_path="/workspace/test")

    assert diff == "scoped\n"
    mock_dl.assert_called_once_with(["/workspace/test/.sessions/scan.log"])


def test_read_session_log_diff_returns_only_new_bytes_on_second_call():
    sandbox = DaemonSandbox(container_name="test")

    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [_file_response("/workspace/.sessions/scan.log", b"old\n")]
        sandbox.read_session_log_diff("scan")

        mock_dl.return_value = [_file_response("/workspace/.sessions/scan.log", b"old\nnew\n")]
        diff = sandbox.read_session_log_diff("scan")

    assert "old" not in diff and "new" in diff


def test_read_session_log_diff_empty_when_no_new_bytes():
    sandbox = DaemonSandbox(container_name="test")

    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [_file_response("/workspace/.sessions/scan.log", b"data\n")]
        sandbox.read_session_log_diff("scan")
        diff = sandbox.read_session_log_diff("scan")

    assert diff == ""


def test_read_session_log_diff_recovers_when_file_truncated():
    sandbox = DaemonSandbox(container_name="test")

    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [_file_response("/workspace/.sessions/scan.log", b"a" * 100)]
        sandbox.read_session_log_diff("scan")

        # File shrank (rotation / external truncation)
        mock_dl.return_value = [_file_response("/workspace/.sessions/scan.log", b"x" * 5)]
        diff = sandbox.read_session_log_diff("scan")

    assert diff == "xxxxx"


def test_reset_session_log_offset_clears_state():
    sandbox = DaemonSandbox(container_name="test")
    sandbox._log_offsets["scan"] = 42
    sandbox.reset_session_log_offset("scan")
    assert "scan" not in sandbox._log_offsets


def test_read_session_log_diff_returns_empty_when_file_missing():
    sandbox = DaemonSandbox(container_name="test")
    with patch.object(sandbox, "download_files") as mock_dl:
        mock_dl.return_value = [
            FileDownloadResponse(
                path="/workspace/.sessions/scan.log",
                content=None,
                error="file_not_found",
            ),
        ]
        diff = sandbox.read_session_log_diff("scan")
    assert diff == ""


def test_read_session_log_diff_concurrent_does_not_double_count():
    """20 threads reading the same session log must collectively consume
    each byte exactly once — no overlap, no gaps."""
    sandbox = DaemonSandbox(container_name="test")
    payload = b"x" * 1000

    def fake_download(paths):
        return [_file_response(paths[0], payload)]

    with patch.object(sandbox, "download_files", side_effect=fake_download):
        barrier = threading.Barrier(20)
        results: list[str] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            r = sandbox.read_session_log_diff("scan")
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # Exactly one thread sees the full payload; the other 19 see ""
    full_reads = [r for r in results if r]
    empty_reads = [r for r in results if not r]
    assert len(full_reads) == 1
    assert len(empty_reads) == 19
    assert full_reads[0] == "x" * 1000
    assert sandbox._log_offsets["scan"] == 1000


def test_kill_session_sends_ctrl_c_then_kill_session_then_clears_caches():
    sandbox = DaemonSandbox(container_name="test")
    sandbox._jobs.register("scan", command="x", initial_markers=1)
    sandbox._log_offsets["scan"] = 42
    mgr = sandbox._get_manager("scan")  # populate the cache
    TmuxSessionManager._initialized.add("scan")

    with patch.object(mgr, "_tmux") as mock_tmux:
        sandbox.kill_session("scan")

    sent_calls = [c.args[0] for c in mock_tmux.call_args_list]

    # Ordering: send-keys C-c MUST come before kill-session
    ctrl_c_idx = next(i for i, c in enumerate(sent_calls) if c[0] == "send-keys" and c[-1] == "C-c")
    kill_idx = next(i for i, c in enumerate(sent_calls) if c[0] == "kill-session")
    assert ctrl_c_idx < kill_idx, "send-keys C-c must be issued before kill-session"

    # All caches cleared
    assert "scan" not in sandbox._managers
    assert "scan" not in TmuxSessionManager._initialized
    assert "scan" not in sandbox._log_offsets
    assert sandbox._jobs.get("scan") is None


def test_kill_session_swallows_errors():
    sandbox = DaemonSandbox(container_name="test")
    sandbox._jobs.register("flaky", command="x", initial_markers=1)
    sandbox._log_offsets["flaky"] = 7
    mgr = sandbox._get_manager("flaky")
    TmuxSessionManager._initialized.add("flaky")

    with patch.object(mgr, "_tmux", side_effect=RuntimeError("boom")):
        sandbox.kill_session("flaky")  # must not raise

    # Caches still cleared even when tmux ops failed
    assert "flaky" not in sandbox._managers
    assert "flaky" not in TmuxSessionManager._initialized
    assert "flaky" not in sandbox._log_offsets
    assert sandbox._jobs.get("flaky") is None


def test_kill_all_sessions_kills_every_manager_and_clears_caches():
    sandbox = DaemonSandbox(container_name="test")
    # Populate three independent sessions across two workspaces so we
    # exercise both the root-workspace and engagement-scoped manager keys.
    mgrs = [
        sandbox._get_manager("alpha"),
        sandbox._get_manager("beta"),
        sandbox._get_manager("gamma", workspace_path="/workspace/eng-1"),
    ]
    TmuxSessionManager._initialized.update(m.session for m in mgrs)
    sandbox._log_offsets["alpha"] = 1
    sandbox._log_offsets["beta"] = 2

    # Patch every manager's _tmux to record kill-server calls.
    with (
        patch.object(mgrs[0], "_tmux") as t0,
        patch.object(mgrs[1], "_tmux") as t1,
        patch.object(mgrs[2], "_tmux") as t2,
    ):
        killed = sandbox.kill_all_sessions()

    assert killed == 3, "every active session should have kill-server invoked"
    for t in (t0, t1, t2):
        sent = [c.args[0] for c in t.call_args_list]
        assert ["kill-server"] in sent, f"kill-server not invoked for one of the sessions: {sent}"

    # Manager cache fully drained.
    assert sandbox._managers == {}
    # _initialized set drained of every session we created.
    for mgr in mgrs:
        assert mgr.session not in TmuxSessionManager._initialized
    # Log offsets for the cleared manager keys are gone.
    assert "alpha" not in sandbox._log_offsets
    assert "beta" not in sandbox._log_offsets


def test_kill_all_sessions_continues_past_individual_failures():
    """One stuck session must not block the rest from being torn down."""
    sandbox = DaemonSandbox(container_name="test")
    m1 = sandbox._get_manager("good-1")
    m2 = sandbox._get_manager("bad")
    m3 = sandbox._get_manager("good-2")

    with (
        patch.object(m1, "_tmux") as t1,
        patch.object(m2, "_tmux", side_effect=RuntimeError("tmux server gone")) as t2,
        patch.object(m3, "_tmux") as t3,
    ):
        killed = sandbox.kill_all_sessions()

    # Only the two healthy sessions count as successfully killed.
    assert killed == 2
    # All three were attempted.
    assert t1.called and t2.called and t3.called
    # Manager cache cleared regardless of per-session failures.
    assert sandbox._managers == {}


def test_kill_all_sessions_is_noop_when_no_managers():
    sandbox = DaemonSandbox(container_name="test")
    assert sandbox.kill_all_sessions() == 0


def test_read_screen_handles_capture_timeout_gracefully():
    """read_screen() must NOT crash on subprocess.TimeoutExpired from capture-pane."""
    mgr = TmuxSessionManager("hung", "decepticon-sandbox")
    TmuxSessionManager._initialized.add("hung")  # skip initialize path

    with patch.object(
        mgr, "_capture", side_effect=_sp.TimeoutExpired(cmd="docker exec", timeout=10)
    ):
        result = mgr.read_screen()

    # Must return a string, not raise. Should signal trouble to the agent.
    assert isinstance(result, str)
    assert "[ERROR]" in result or "[TIMEOUT]" in result


def test_execute_async_baseline_capture_timeout_does_not_escape():
    """execute_async() initial baseline _capture timing out must not raise."""
    import asyncio

    mgr = TmuxSessionManager("hung", "decepticon-sandbox")
    TmuxSessionManager._initialized.add("hung")

    with patch.object(
        mgr, "_capture", side_effect=_sp.TimeoutExpired(cmd="docker exec", timeout=10)
    ):
        # Patch initialize so the recovery path doesn't try real tmux.
        with patch.object(mgr, "initialize", return_value=None):
            result = asyncio.run(mgr.execute_async(command="ls", is_input=False, timeout=2))

    assert isinstance(result, str)
    assert "[ERROR]" in result


def test_execute_async_poll_loop_capture_timeout_continues():
    """A transient TimeoutExpired from capture-pane during polling must NOT
    escape; loop should continue (and eventually time out cleanly)."""
    import asyncio

    mgr = TmuxSessionManager("flaky", "decepticon-sandbox")
    TmuxSessionManager._initialized.add("flaky")

    # Baseline succeeds; subsequent polls raise TimeoutExpired forever.
    captures = ["[DCPTN:0:/tmp] "] + [_sp.TimeoutExpired(cmd="docker exec", timeout=10)] * 100

    def fake_capture():
        v = captures.pop(0)
        if isinstance(v, BaseException):
            raise v
        return v

    with (
        patch.object(mgr, "_capture", side_effect=fake_capture),
        patch.object(mgr, "_send", return_value=None),
        patch.object(mgr, "initialize", return_value=None),
        patch("decepticon.sandbox_kernel.tmux.POLL_INTERVAL", 0.01),
    ):
        result = asyncio.run(mgr.execute_async(command="ls", is_input=False, timeout=1))

    assert isinstance(result, str)
    # Either [TIMEOUT] (loop ran out) or [ERROR] (poll path bailed out) is acceptable
    assert "[TIMEOUT]" in result or "[ERROR]" in result


def test_poll_loop_capture_errors_dont_falsely_trigger_stall_detection():
    """A burst of transient TimeoutExpired during polling must not cause
    stall detection to mis-fire when the next successful capture shows
    a non-baseline screen (e.g. command produced output earlier)."""
    import asyncio

    mgr = TmuxSessionManager("loaded", "decepticon-sandbox")
    TmuxSessionManager._initialized.add("loaded")

    # Sequence: baseline → 3 transient timeouts → success with new marker (DONE)
    captures = [
        "[DCPTN:0:/tmp] ",  # baseline (1 marker)
        _sp.TimeoutExpired(cmd="docker exec", timeout=10),  # poll 1
        _sp.TimeoutExpired(cmd="docker exec", timeout=10),  # poll 2
        _sp.TimeoutExpired(cmd="docker exec", timeout=10),  # poll 3
        "[DCPTN:0:/tmp] ls\noutput line\n[DCPTN:0:/tmp] ",  # poll 4: success, NEW marker -> command done
    ]

    def fake_capture():
        v = captures.pop(0)
        if isinstance(v, BaseException):
            raise v
        return v

    with (
        patch.object(mgr, "_capture", side_effect=fake_capture),
        patch.object(mgr, "_send", return_value=None),
        patch.object(mgr, "_clear_screen", return_value=None),
        patch.object(mgr, "initialize", return_value=None),
        patch("decepticon.sandbox_kernel.tmux.POLL_INTERVAL", 0.01),
        patch("decepticon.sandbox_kernel.tmux.STALL_SECONDS", 0.05),
    ):
        result = asyncio.run(mgr.execute_async(command="ls", is_input=False, timeout=2))

    # Must reach the DONE branch, not falsely return [interactive].
    assert "interactive" not in result, f"False stall detected: {result!r}"
    # And not [TIMEOUT] either — the loop should have detected completion.
    assert "[TIMEOUT]" not in result, f"Should have completed: {result!r}"
    assert "[ERROR]" not in result, f"Should not have errored: {result!r}"


def test_initialize_recreates_stale_cached_pane_without_error_string_matching():
    mgr = TmuxSessionManager("stale", "decepticon-sandbox")
    mgr._pane_id = "%old"
    TmuxSessionManager._initialized.add("stale")

    with (
        patch.object(mgr, "_sync_passthrough_env"),
        patch.object(mgr, "_tmux") as mock_tmux,
        patch("decepticon.sandbox_kernel.base.subprocess.run") as mock_run,
        patch("time.sleep"),
    ):
        mock_tmux.side_effect = [
            RuntimeError("arbitrary tmux target failure"),  # cached pane verification
            RuntimeError("arbitrary missing session failure"),  # has-session
            "%new",  # new-session -P -F '#{pane_id}'
            "",  # send-keys ps1_cmd
            "",  # send-keys Enter
            "",  # send-keys C-l
            "",  # clear-history
            "",  # pipe-pane
            "",  # _sync_passthrough_env -> _send -> send-keys -l
            "",  # _sync_passthrough_env -> _send -> send-keys Enter
        ]
        mock_run.return_value.returncode = 0
        mgr.initialize()

    assert mgr._pane_id == "%new"
    assert "stale" in TmuxSessionManager._initialized
    # Since df752a3, _target() returns the session name unconditionally to
    # avoid the parallel-N pane-id race. send-keys must address the session
    # ("stale"), not the pane id ("%new").
    sent_targets = [c.args[0][2] for c in mock_tmux.call_args_list if c.args[0][0] == "send-keys"]
    assert sent_targets and all(t == "stale" for t in sent_targets)


def test_execute_recovers_initial_capture_failure_without_error_string_matching():
    mgr = TmuxSessionManager("recover", "decepticon-sandbox")
    mgr._pane_id = "%bad"
    TmuxSessionManager._initialized.add("recover")

    with (
        patch.object(mgr, "initialize", return_value=None) as mock_initialize,
        patch.object(mgr, "_capture") as mock_capture,
        patch.object(mgr, "_send", return_value=None),
        patch.object(mgr, "_clear_screen", return_value=None),
        patch("time.sleep"),
    ):
        mock_capture.side_effect = [
            RuntimeError("arbitrary tmux capture failure"),
            "[DCPTN:0:/workspace] ",
            "[DCPTN:0:/workspace] ls\nfile.txt\n[DCPTN:0:/workspace] ",
        ]
        result = mgr.execute(command="ls", is_input=False, timeout=2)

    assert mock_initialize.call_count == 2
    assert mgr._pane_id is None
    assert "recover" not in TmuxSessionManager._initialized
    assert "[ERROR]" not in result
    assert "file.txt" in result
