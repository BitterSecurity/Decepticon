"""Stale-poll guard tests for the bash tool.

The guard injects a [STALE] reminder after `_STALE_PASSIVE_READS` consecutive
identical passive reads on the same (workspace, session). It must reset on any
state-changing event: non-empty command, output diff, kill, or new background
job. The guard targets a documented anti-pattern (wedged-shell polling) and is
mechanically enforced rather than left as prompt advice.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from decepticon_sandbox.kernel import BackgroundJobTracker

from decepticon.tools.bash.bash import (
    _STALE_PASSIVE_READS,
    _passive_read_state,
    _reset_passive_read,
    _track_passive_read,
    bash,
    bash_kill,
    bash_output,
    set_sandbox,
)


def _fake_sandbox():
    sandbox = MagicMock()
    sandbox._jobs = BackgroundJobTracker()
    return sandbox


def setup_function():
    _passive_read_state.clear()


def test_stale_hint_fires_only_at_threshold():
    for _ in range(_STALE_PASSIVE_READS - 1):
        assert _track_passive_read("/w", "main", "same") is None
    hint = _track_passive_read("/w", "main", "same")
    assert hint is not None
    assert "[STALE]" in hint
    assert "main" in hint


def test_changing_output_resets_counter():
    for _ in range(_STALE_PASSIVE_READS - 1):
        _track_passive_read("/w", "main", "same")
    _track_passive_read("/w", "main", "different")
    # Now back to 1 same: threshold should NOT fire on next identical
    assert _track_passive_read("/w", "main", "same") is None


def test_reset_passive_read_clears_state():
    for _ in range(_STALE_PASSIVE_READS):
        _track_passive_read("/w", "main", "x")
    _reset_passive_read("/w", "main")
    assert _track_passive_read("/w", "main", "x") is None


def test_per_session_state_isolation():
    for _ in range(_STALE_PASSIVE_READS):
        _track_passive_read("/w", "main", "x")
    # Different session should not have fired
    assert _track_passive_read("/w", "other", "x") is None


def test_per_workspace_state_isolation():
    for _ in range(_STALE_PASSIVE_READS):
        _track_passive_read("/wA", "main", "x")
    assert _track_passive_read("/wB", "main", "x") is None


def test_bash_empty_command_passive_read_emits_stale_hint():
    sandbox = _fake_sandbox()
    sandbox.execute_tmux_async = AsyncMock(return_value="idle prompt")
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS - 1):
        result = asyncio.run(
            bash.ainvoke(
                {"command": "", "session": "scan", "description": "Read the current screen"}
            )
        )
        assert "[STALE]" not in result
    result = asyncio.run(
        bash.ainvoke({"command": "", "session": "scan", "description": "Read the current screen"})
    )
    assert "[STALE]" in result


def test_bash_non_empty_command_resets_counter():
    sandbox = _fake_sandbox()
    sandbox.execute_tmux_async = AsyncMock(return_value="idle prompt")
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS):
        asyncio.run(
            bash.ainvoke(
                {"command": "", "session": "scan", "description": "Read the current screen"}
            )
        )
    # Reset via real command
    sandbox.execute_tmux_async = AsyncMock(return_value="fresh output")
    asyncio.run(
        bash.ainvoke({"command": "ls", "session": "scan", "description": "List the directory"})
    )
    # Subsequent passive read should NOT fire (counter cleared)
    sandbox.execute_tmux_async = AsyncMock(return_value="idle prompt")
    result = asyncio.run(
        bash.ainvoke({"command": "", "session": "scan", "description": "Read the current screen"})
    )
    assert "[STALE]" not in result


def test_bash_output_running_no_diff_emits_stale_hint():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    sandbox.read_session_log_diff = MagicMock(return_value="")
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS - 1):
        result = asyncio.run(bash_output.ainvoke({"session": "scan"}))
        assert "[STALE]" not in result
    result = asyncio.run(bash_output.ainvoke({"session": "scan"}))
    assert "[STALE]" in result


def test_bash_output_diff_resets_counter():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    sandbox.read_session_log_diff = MagicMock(return_value="")
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS):
        asyncio.run(bash_output.ainvoke({"session": "scan"}))
    # New bytes arrive
    sandbox.read_session_log_diff = MagicMock(return_value="fresh bytes")
    asyncio.run(bash_output.ainvoke({"session": "scan"}))
    # Back to empty — counter must restart, not immediately fire
    sandbox.read_session_log_diff = MagicMock(return_value="")
    result = asyncio.run(bash_output.ainvoke({"session": "scan"}))
    assert "[STALE]" not in result


def test_bash_kill_resets_counter():
    sandbox = _fake_sandbox()
    sandbox.execute_tmux_async = AsyncMock(return_value="idle prompt")
    sandbox.kill_session = MagicMock()
    sandbox.session_log_path = MagicMock(return_value="/log/scan.log")
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS):
        asyncio.run(
            bash.ainvoke(
                {"command": "", "session": "scan", "description": "Read the current screen"}
            )
        )
    asyncio.run(bash_kill.ainvoke({"session": "scan"}))
    # After kill, fresh passive read should not fire
    result = asyncio.run(
        bash.ainvoke({"command": "", "session": "scan", "description": "Read the current screen"})
    )
    assert "[STALE]" not in result


def test_background_launch_resets_counter():
    sandbox = _fake_sandbox()
    sandbox.execute_tmux_async = AsyncMock(return_value="idle prompt")
    sandbox.start_background = MagicMock()
    set_sandbox(sandbox)

    for _ in range(_STALE_PASSIVE_READS):
        asyncio.run(
            bash.ainvoke(
                {"command": "", "session": "scan", "description": "Read the current screen"}
            )
        )
    asyncio.run(
        bash.ainvoke(
            {
                "command": "sleep 30",
                "session": "scan",
                "background": True,
                "description": "Sleep in the background",
            }
        )
    )
    result = asyncio.run(
        bash.ainvoke({"command": "", "session": "scan", "description": "Read the current screen"})
    )
    assert "[STALE]" not in result
