"""bash_output / bash_kill / bash_status tool unit tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from decepticon.sandbox_kernel import BackgroundJobTracker
from decepticon.tools.bash.bash import (
    bash,
    bash_kill,
    bash_output,
    bash_status,
    bash_workspace,
    set_sandbox,
)


def test_runtime_config_is_not_part_of_model_tool_schemas():
    for tool in (bash, bash_output, bash_kill, bash_status):
        assert "config" not in tool.tool_call_schema.model_json_schema()["properties"]


def _fake_sandbox():
    sandbox = MagicMock()
    sandbox._jobs = BackgroundJobTracker()
    return sandbox


def test_bash_output_running_job_returns_running_marker():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    sandbox.read_session_log_diff = MagicMock(return_value="partial output")
    set_sandbox(sandbox)

    result = asyncio.run(bash_output.ainvoke({"session": "scan"}))

    assert "[RUNNING" in result
    assert "partial output" in result


def test_bash_output_done_job_marks_consumed_and_exposes_exit_code():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox._jobs.mark_complete("scan", exit_code=0)
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    sandbox.read_session_log_diff = MagicMock(return_value="full nmap output")
    set_sandbox(sandbox)

    result = asyncio.run(bash_output.ainvoke({"session": "scan"}))

    assert "[DONE" in result
    assert "exit=0" in result
    assert sandbox._jobs.get("scan").consumed is True


def test_bash_output_idle_when_no_job_registered():
    sandbox = _fake_sandbox()
    sandbox.poll_completion = MagicMock(return_value=None)
    sandbox.read_session_log_diff = MagicMock(return_value="")
    set_sandbox(sandbox)

    result = asyncio.run(bash_output.ainvoke({"session": "never-seen"}))

    assert "[IDLE]" in result


def test_bash_kill_invokes_sandbox_kill_session():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap", initial_markers=1)
    sandbox.kill_session = MagicMock()
    set_sandbox(sandbox)

    result = asyncio.run(bash_kill.ainvoke({"session": "scan"}))

    sandbox.kill_session.assert_called_once_with("scan")
    assert "[KILLED]" in result
    assert "scan" in result


def test_bash_status_lists_running_and_done_jobs():
    sandbox = _fake_sandbox()
    sandbox._jobs.register("scan", command="nmap target", initial_markers=1)
    sandbox._jobs.register("brute", command="hydra ...", initial_markers=1)
    sandbox._jobs.mark_complete("brute", exit_code=1)
    sandbox.poll_completion = MagicMock(side_effect=lambda s: sandbox._jobs.get(s))
    set_sandbox(sandbox)

    result = asyncio.run(bash_status.ainvoke({}))

    assert "scan" in result and "running" in result
    assert "brute" in result and "exit=1" in result


def test_bash_status_empty_returns_empty_marker():
    sandbox = _fake_sandbox()
    set_sandbox(sandbox)
    with bash_workspace("/workspace/test"):
        result = asyncio.run(bash_status.ainvoke({}))
    assert "[EMPTY]" in result


def test_bash_background_uses_engagement_workspace_context():
    sandbox = _fake_sandbox()
    sandbox.execute = MagicMock()
    sandbox.start_background = MagicMock()
    set_sandbox(sandbox)

    with bash_workspace("/workspace/test"):
        result = asyncio.run(
            bash.ainvoke(
                {
                    "command": "sleep 1",
                    "background": True,
                    "session": "scan",
                    "description": "Sleep in the background",
                }
            )
        )

    sandbox.start_background.assert_called_once_with(
        command="sleep 1",
        session="scan",
        workspace_path="/workspace/test",
    )
    assert "[BACKGROUND]" in result


def test_bash_uses_engagement_workspace_from_environment(monkeypatch):
    sandbox = _fake_sandbox()
    sandbox.execute = MagicMock()
    sandbox.execute_tmux_async = AsyncMock(return_value="ok")
    set_sandbox(sandbox)
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "env-engagement")

    result = asyncio.run(bash.ainvoke({"command": "pwd", "description": "Print working directory"}))

    sandbox.execute_tmux_async.assert_called_once_with(
        command="pwd",
        session="main",
        timeout=120,
        is_input=False,
        workspace_path="/workspace/env-engagement",
    )
    assert result == "ok"


def test_large_output_without_engagement_workspace_does_not_create_root_scratch():
    sandbox = _fake_sandbox()
    sandbox.execute = MagicMock()
    sandbox.execute_tmux_async = AsyncMock(return_value="x" * 15_001)
    set_sandbox(sandbox)

    result = asyncio.run(
        bash.ainvoke(
            {"command": "big", "description": "Produce large output"},
            config={"configurable": {"workspace_path": "/workspace"}},
        )
    )

    assert "/workspace/.scratch" not in result
    assert "not written" in result
    sandbox.execute.assert_not_called()
