from unittest.mock import patch

import pytest

from decepticon.sandbox_kernel.tmux import TmuxCommandError, TmuxSessionManager


def test_first_command_returns_its_output_when_initial_export_is_delayed() -> None:
    # Given: a shell processes one queued command per poll, including the export.
    manager = TmuxSessionManager("init-env-regression", "ctn")
    pending: list[str] = []
    screen = "[DCPTN:0:/workspace] "

    def send(command: str, enter: bool = True) -> None:
        pending.append(command)

    def advance_shell(seconds: float) -> None:
        nonlocal screen
        if pending:
            command = pending.pop(0)
            output = "first-command-result\n" if command == "run-probe" else ""
            screen += f"{command}\n{output}[DCPTN:0:/workspace] "

    with (
        patch.object(manager, "_cached_pane_is_alive", return_value=False),
        patch.object(manager, "_ensure_session", return_value=True),
        patch.object(manager, "_inject_ps1_marker"),
        patch.object(manager, "_clear_screen"),
        patch.object(manager, "_capture", side_effect=lambda: screen),
        patch.object(manager, "_send", side_effect=send),
        patch("decepticon.sandbox_kernel.tmux.time.sleep", side_effect=advance_shell),
        patch(
            "decepticon.sandbox_kernel.tmux._allowed_passthrough_env",
            return_value={"DECEPTICON_BROWSER_TEST": "fixture"},
        ),
    ):
        try:
            # When: the first command runs immediately after initialization.
            result = manager.execute("run-probe", is_input=False, timeout=10)
        finally:
            TmuxSessionManager._initialized.discard(manager.session)

    # Then: completion belongs to the user command, with no export echo.
    assert result == "first-command-result\n[cwd: /workspace]"


def test_first_command_is_not_sent_when_initial_export_times_out() -> None:
    manager = TmuxSessionManager("init-env-timeout", "ctn")
    with (
        patch.object(manager, "_cached_pane_is_alive", return_value=False),
        patch.object(manager, "_ensure_session", return_value=True),
        patch.object(manager, "_inject_ps1_marker"),
        patch.object(manager, "_capture", return_value="[DCPTN:0:/workspace] "),
        patch.object(manager, "_send") as send,
        patch("decepticon.sandbox_kernel.tmux.time.monotonic", side_effect=[0.0, 10.0]),
        patch(
            "decepticon.sandbox_kernel.tmux._allowed_passthrough_env",
            return_value={"DECEPTICON_BROWSER_TEST": "fixture"},
        ),
    ):
        with pytest.raises(TmuxCommandError):
            manager.execute("run-probe", is_input=False, timeout=10)

    assert all(call.args[0] != "run-probe" for call in send.call_args_list)
    assert manager.session not in TmuxSessionManager._initialized
