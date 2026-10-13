"""Background job tracker — pure-Python unit tests + start/poll integration."""

import asyncio
import subprocess
from unittest.mock import patch

import pytest

from decepticon.sandbox_kernel import BackgroundJobTracker, TmuxSessionManager
from decepticon.sandbox_kernel.daemon import DaemonSandbox


def test_register_records_command_and_marker_count():
    tracker = BackgroundJobTracker()
    job = tracker.register("scan-1", command="nmap target", initial_markers=3)

    assert job.session == "scan-1"
    assert job.command == "nmap target"
    assert job.initial_markers == 3
    assert job.status == "running"
    assert job.exit_code is None
    assert job.consumed is False


def test_mark_complete_records_exit_code():
    tracker = BackgroundJobTracker()
    tracker.register("scan-1", command="nmap target", initial_markers=3)
    tracker.mark_complete("scan-1", exit_code=0)
    job = tracker.get("scan-1")
    assert job is not None
    assert job.status == "done"
    assert job.exit_code == 0
    assert job.completed_at is not None
    assert job.consumed is False


def test_mark_consumed_after_output_retrieval():
    tracker = BackgroundJobTracker()
    tracker.register("scan-1", command="nmap target", initial_markers=3)
    tracker.mark_complete("scan-1", exit_code=0)
    tracker.mark_consumed("scan-1")
    job = tracker.get("scan-1")
    assert job is not None
    assert job.consumed is True


def test_pending_completions_returns_done_unconsumed_only():
    tracker = BackgroundJobTracker()
    tracker.register("a", command="x", initial_markers=1)
    tracker.register("b", command="y", initial_markers=1)
    tracker.register("c", command="z", initial_markers=1)
    tracker.mark_complete("a", exit_code=0)
    tracker.mark_complete("b", exit_code=1)
    tracker.mark_consumed("a")
    pending = tracker.pending_completions()
    assert [j.session for j in pending] == ["b"]


def test_register_replaces_previous_job_in_same_session():
    tracker = BackgroundJobTracker()
    tracker.register("scan", command="first", initial_markers=1)
    tracker.mark_complete("scan", exit_code=0)
    tracker.register("scan", command="second", initial_markers=2)
    job = tracker.get("scan")
    assert job is not None
    assert job.command == "second"
    assert job.status == "running"


def test_remove_drops_session_entry():
    tracker = BackgroundJobTracker()
    tracker.register("scan", command="x", initial_markers=1)
    tracker.remove("scan")
    assert tracker.get("scan") is None


def test_start_background_registers_owned_generation() -> None:
    sandbox = DaemonSandbox(container_name="test")
    mgr = TmuxSessionManager("scan", "test")
    with (
        patch.object(sandbox, "_get_manager", return_value=mgr),
        patch.object(mgr, "initialize"),
        patch.object(mgr, "_capture", return_value="[DCPTN:0:/workspace] "),
        patch.object(mgr, "_tmux", return_value="123"),
        patch.object(mgr, "_send"),
    ):
        sandbox.start_background("printf fixture", session="scan")
    job = sandbox._jobs.get("scan")
    assert job is not None and job.generation == mgr._generation
    assert job.generation is not None
    assert job.status == "running"


@pytest.mark.parametrize(
    "native_record, expected",
    [
        (f"{'a' * 32}:123:7", "done"),
        ("", "running"),
        (f"{'b' * 32}:123:0", "running"),
    ],
)
def test_background_completion_uses_owned_native_record(native_record: str, expected: str) -> None:
    sandbox = DaemonSandbox(container_name="test")
    job = sandbox._jobs.register("scan", command="printf fixture", initial_markers=1)
    job.generation = "a" * 32
    mgr = TmuxSessionManager("scan", "test")
    mgr._generation = job.generation
    mgr._shell_pid = "123"
    with (
        patch.object(sandbox, "_get_manager", return_value=mgr),
        patch.object(mgr, "_tmux", return_value=f"123\t{native_record}:/workspace"),
    ):
        sandbox.poll_completion("scan")
    assert job.status == expected
    assert job.exit_code == (7 if expected == "done" else None)


def test_poll_completion_returns_none_for_unknown_session() -> None:
    sandbox = DaemonSandbox(container_name="test")
    assert sandbox.poll_completion("never-seen") is None


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(cmd="tmux", timeout=10),
        RuntimeError("no server running"),
    ],
)
def test_poll_completion_keeps_running_when_native_state_unavailable(failure: Exception) -> None:
    sandbox = DaemonSandbox(container_name="test")
    job = sandbox._jobs.register("scan", command="printf fixture", initial_markers=1)
    job.generation = "a" * 32
    mgr = TmuxSessionManager("scan", "test")
    mgr._generation = job.generation
    mgr._shell_pid = "123"
    with (
        patch.object(sandbox, "_get_manager", return_value=mgr),
        patch.object(mgr, "_tmux", side_effect=failure),
    ):
        sandbox.poll_completion("scan")
    assert job.status == "running" and job.exit_code is None


def test_auto_background_path_registers_owned_generation() -> None:
    sandbox = DaemonSandbox(container_name="test")
    mgr = TmuxSessionManager("long", "test")

    def native_state(args: list[str], timeout: int = 10) -> str:
        return "123" if args[-1] == "#{pane_pid}" else "123\t\t/workspace"

    with (
        patch.object(sandbox, "_get_manager", return_value=mgr),
        patch.object(mgr, "initialize"),
        patch.object(mgr, "_capture", return_value="[DCPTN:0:/workspace] "),
        patch.object(mgr, "_send"),
        patch.object(mgr, "_tmux", side_effect=native_state),
        patch("decepticon.sandbox_kernel.tmux.AUTO_BACKGROUND_SECONDS", 0.0),
        patch("decepticon.sandbox_kernel.tmux.POLL_INTERVAL", 0.01),
    ):
        output = asyncio.run(sandbox.execute_tmux_async("sleep 9", session="long", timeout=2))
    job = sandbox._jobs.get("long")
    assert job is not None and job.generation == mgr._generation
    assert job.generation is not None
    assert job.status == "running"
    assert output.endswith("[DCPTN_STATUS incomplete]")
