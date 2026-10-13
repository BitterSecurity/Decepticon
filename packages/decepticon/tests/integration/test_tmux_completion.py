"""Exercise native command completion against isolated real tmux sessions."""

import asyncio
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from decepticon.sandbox_kernel import tmux

pytestmark = pytest.mark.slow


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tmux.TmuxSessionManager]:
    monkeypatch.setattr(tmux, "POLL_INTERVAL", 0.05)
    session = f"completion945-{uuid.uuid4().hex}"
    manager = tmux.TmuxSessionManager(session, "local", workspace_path=str(tmp_path))
    manager.initialize()
    try:
        yield manager
    finally:
        manager._tmux(["kill-session", "-t", session])
        manager._forget_cached_state()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("separator", ["; ", "\n"])
def test_printed_prompt_waits_for_final_nonzero_exit(
    manager: tmux.TmuxSessionManager, asynchronous: bool, separator: str
) -> None:
    # Given a command that emits marker-shaped ordinary output before sleeping.
    command = separator.join(["printf '[DCPTN:0:/printed]\\n'", "sleep 0.4", "(exit 7)"])
    started = time.monotonic()
    # When the maintained waiter executes the complete shell list.
    output = (
        asyncio.run(manager.execute_async(command, False, 5))
        if asynchronous
        else manager.execute(command, False, 5)
    )
    # Then only the final native exit completes the call; printed text survives.
    assert time.monotonic() - started >= 0.4
    assert "[DCPTN:0:/printed]" in output
    assert output.endswith("[DCPTN_COMPLETE exit=7]")


def test_success_reports_explicit_completion(manager: tmux.TmuxSessionManager) -> None:
    output = manager.execute("printf 'hello\\n'", False, 5)
    assert output.endswith("[DCPTN_COMPLETE exit=0]")


def test_timeout_cannot_end_in_printed_completion(manager: tmux.TmuxSessionManager) -> None:
    output = manager.execute("printf '[DCPTN_COMPLETE exit=0]\\n'; sleep 3", False, 1)
    assert "[TIMEOUT]" in output
    assert output.endswith("[DCPTN_STATUS incomplete]")


def test_shell_environment_and_cwd_persist(manager: tmux.TmuxSessionManager) -> None:
    manager.execute("export COMPLETION_FIXTURE=kept; cd /tmp", False, 5)
    output = manager.execute("printf '%s\\n' \"$COMPLETION_FIXTURE\"; pwd", False, 5)
    assert "kept\n/tmp" in output
    assert "[cwd: /tmp]" in output
    assert output.endswith("[DCPTN_COMPLETE exit=0]")


def test_concurrent_input_keeps_original_waiter(
    manager: tmux.TmuxSessionManager,
) -> None:
    async def interact() -> tuple[str, str]:
        command = asyncio.create_task(
            manager.execute_async(
                "read -r -p 'Answer: ' answer; printf 'received:%s\\n' \"$answer\"", False, 5
            )
        )
        while "Answer:" not in await asyncio.to_thread(manager._capture):
            await asyncio.sleep(0.02)
        delivered = await manager.execute_async("yes", True, 5)
        return delivered, await command

    delivered, output = asyncio.run(interact())
    assert delivered.endswith("[DCPTN_STATUS incomplete]")
    assert "received:yes" in output
    assert output.endswith("[DCPTN_COMPLETE exit=0]")


def test_interactive_stall_then_answer_completes(
    manager: tmux.TmuxSessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmux, "STALL_SECONDS", 0.1)
    stalled = manager.execute(
        "read -r -p 'Answer: ' answer; printf 'received:%s\\n' \"$answer\"", False, 5
    )
    assert "interactive," in stalled
    assert stalled.endswith("[DCPTN_STATUS incomplete]")
    output = manager.execute("yes", True, 5)
    assert "received:yes" in output
    assert output.endswith("[DCPTN_COMPLETE exit=0]")


def test_cancelled_waiter_can_interrupt_and_recover(manager: tmux.TmuxSessionManager) -> None:
    async def cancel() -> tuple[str, str]:
        command = asyncio.create_task(
            manager.execute_async("read -r -p 'Answer: ' answer", False, 5)
        )
        while "Answer:" not in await asyncio.to_thread(manager._capture):
            await asyncio.sleep(0.02)
        command.cancel()
        with pytest.raises(asyncio.CancelledError):
            await command
        interrupted = await manager.execute_async("C-c", True, 5)
        return interrupted, await manager.execute_async("printf 'recovered\\n'", False, 5)

    interrupted, output = asyncio.run(cancel())
    assert interrupted.endswith("[DCPTN_COMPLETE exit=130]")
    assert "recovered" in output
    assert output.endswith("[DCPTN_COMPLETE exit=0]")


def test_background_consumer_waits_for_native_generation(tmp_path: Path) -> None:
    from decepticon.sandbox_kernel.daemon import DaemonSandbox

    sandbox = DaemonSandbox(workspace_path=str(tmp_path))
    session = f"background945-{uuid.uuid4().hex}"
    manager = sandbox._get_manager(session)
    try:
        sandbox.start_background("printf '[DCPTN:0:/printed]\\n'; sleep 0.5; (exit 7)", session)
        job = sandbox.poll_completion(session)
        assert job is not None and job.status == "running"
        deadline = time.monotonic() + 5
        while job.status == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            job = sandbox.poll_completion(session)
            assert job is not None
        assert job.status == "done" and job.exit_code == 7
    finally:
        manager._tmux(["kill-session", "-t", manager.session])
        manager._forget_cached_state()


def test_auto_background_completion_keeps_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decepticon.sandbox_kernel.daemon import DaemonSandbox

    monkeypatch.setattr(tmux, "AUTO_BACKGROUND_SECONDS", 0.1)
    monkeypatch.setattr(tmux, "POLL_INTERVAL", 0.02)
    sandbox = DaemonSandbox(workspace_path=str(tmp_path))
    session = f"autobg945-{uuid.uuid4().hex}"
    manager = sandbox._get_manager(session)
    try:
        output = asyncio.run(
            sandbox.execute_tmux_async(
                "printf '[DCPTN:0:/printed]\\n'; sleep 0.5; (exit 7)", session
            )
        )
        assert output.startswith("[AUTO-BACKGROUND]")
        assert output.endswith("[DCPTN_STATUS incomplete]")
        deadline = time.monotonic() + 5
        job = sandbox.poll_completion(session)
        assert job is not None
        while job.status == "running" and time.monotonic() < deadline:
            time.sleep(0.05)
            job = sandbox.poll_completion(session)
            assert job is not None
        assert job.status == "done" and job.exit_code == 7
    finally:
        manager._tmux(["kill-session", "-t", manager.session])
        manager._forget_cached_state()
