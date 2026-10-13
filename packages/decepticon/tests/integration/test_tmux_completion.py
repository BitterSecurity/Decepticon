"""Exercise native command completion against isolated real tmux sessions."""

import asyncio
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from decepticon.sandbox_kernel import tmux


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
