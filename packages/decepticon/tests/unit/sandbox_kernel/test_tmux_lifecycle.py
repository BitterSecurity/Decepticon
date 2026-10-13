"""Characterization tests for ``sandbox_kernel.tmux``.

These tests pin the *current* observable behavior of the pure parsing
helpers and the ``TmuxSessionManager`` bookkeeping. CI has no real tmux,
so manager methods are exercised with ``_tmux``/``_capture`` monkey-
patched. Each test asserts a precise observable (parsed cwd, exit code,
exact string, set membership), not just "no crash".

Complements ``tests/unit/backends/test_docker_sandbox_helpers.py`` (which
covers the pure helpers at a coarser level) and the existing pipe-pane
init tests under ``tests/unit/backends/``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from decepticon.sandbox_kernel.tmux import (
    MAX_OUTPUT_CHARS,
    PS1_PATTERN,
    TmuxSessionManager,
    _extract_interactive_output,
    _extract_output,
    _interpret_exit_code,
    _truncate,
)

# ── PS1 marker counting (drives execute() completion detection) ────────


class TestPS1MarkerCounting:
    def test_zero_markers_in_plain_screen(self) -> None:
        assert len(PS1_PATTERN.findall("just\noutput\nlines")) == 0

    def test_single_marker(self) -> None:
        screen = "ls\nfoo\n[DCPTN:0:/workspace] "
        assert len(PS1_PATTERN.findall(screen)) == 1

    def test_three_markers_in_long_session(self) -> None:
        screen = (
            "[DCPTN:0:/workspace] ls\n"
            "a\nb\n"
            "[DCPTN:0:/workspace] cd /tmp\n"
            "[DCPTN:0:/tmp] false\n"
            "[DCPTN:1:/tmp] "
        )
        matches = PS1_PATTERN.findall(screen)
        assert len(matches) == 4
        # Each captured tuple is (exit_code, cwd) — last is the latest prompt.
        assert matches[-1] == ("1", "/tmp")
        assert matches[0] == ("0", "/workspace")

    def test_marker_with_nested_path(self) -> None:
        match = PS1_PATTERN.search("[DCPTN:0:/workspace/a/b/c] ")
        assert match is not None
        assert match.group(2) == "/workspace/a/b/c"

    def test_marker_with_high_exit_code(self) -> None:
        match = PS1_PATTERN.search("[DCPTN:255:/var/log] ")
        assert match is not None
        assert match.group(1) == "255"


# ── _extract_output additional paths ───────────────────────────────────


class TestExtractOutputEdges:
    def test_returns_last_marker_metadata_with_many_markers(self) -> None:
        screen = "[DCPTN:0:/workspace] cmd1\nout1\n[DCPTN:0:/workspace] cmd2\nout2\n[DCPTN:5:/srv] "
        out, exit_code, cwd = _extract_output(screen, command="cmd2")
        assert exit_code == 5
        assert cwd == "/srv"
        # Body taken from the slice between the second-to-last and last marker,
        # with the first echo line (matching the command) stripped.
        assert out == "out2"

    def test_command_echo_with_trailing_whitespace_not_stripped(self) -> None:
        # The stripping check is ``lines[0].strip().endswith(command.strip())``.
        # A line that contains the command in the middle followed by extra
        # tokens does NOT match endswith, so it survives.
        screen = "ls /tmp extra\nresult\n[DCPTN:0:/workspace] "
        out, _, _ = _extract_output(screen, command="ls")
        assert out.splitlines()[0] == "ls /tmp extra"

    def test_empty_command_does_not_strip_first_line(self) -> None:
        screen = "first line\nsecond\n[DCPTN:0:/workspace] "
        out, _, _ = _extract_output(screen, command="")
        # ``command`` is falsy so the strip branch is skipped entirely.
        assert out.splitlines()[0] == "first line"

    def test_only_marker_yields_empty_body(self) -> None:
        out, exit_code, cwd = _extract_output("[DCPTN:0:/workspace] ", command="x")
        assert exit_code == 0
        assert cwd == "/workspace"
        assert out == ""


# ── _truncate additional paths ─────────────────────────────────────────


class TestTruncateEdges:
    def test_just_over_limit_inserts_truncation_marker(self) -> None:
        # At MAX_OUTPUT_CHARS+1 the head+tail still sum to MAX_OUTPUT_CHARS,
        # so the marker text means the result may be slightly LONGER than
        # the input — what we pin is the presence of the marker, not size.
        text = "x" * (MAX_OUTPUT_CHARS + 1)
        result = _truncate(text)
        assert "truncated" in result
        # Far over the limit the result MUST shrink.
        big = "x" * (MAX_OUTPUT_CHARS * 3)
        big_result = _truncate(big)
        assert len(big_result) < len(big)

    def test_truncated_result_reports_correct_mid_chars(self) -> None:
        head = "H" * 100
        mid = "M" * MAX_OUTPUT_CHARS
        tail = "T" * 100
        text = head + mid + tail
        head_chars = int(MAX_OUTPUT_CHARS * 0.6)
        tail_chars = MAX_OUTPUT_CHARS - head_chars
        expected_mid_chars = len(text) - head_chars - tail_chars
        result = _truncate(text)
        assert f"{expected_mid_chars} chars truncated" in result

    def test_truncation_preserves_first_head_chars_verbatim(self) -> None:
        text = "ABCDE" + "x" * (MAX_OUTPUT_CHARS * 2)
        result = _truncate(text)
        # Head split is asymmetric (60% of MAX_OUTPUT_CHARS); the literal
        # prefix must survive byte-for-byte at the start of the result.
        assert result.startswith("ABCDE")


# ── _interpret_exit_code additional paths ──────────────────────────────


class TestInterpretExitCodeEdges:
    def test_known_code_format_starts_with_em_dash(self) -> None:
        # Pinned format: leading " — " (space, en/em-dash, space).
        msg = _interpret_exit_code(127)
        assert msg.startswith(" — ")

    def test_signal_arithmetic_for_unknown_high_codes(self) -> None:
        # 128 + 9 = SIGKILL (137) is in the table; pick 128 + 5 (SIGTRAP)
        # which is not table-mapped to verify the fallback branch.
        msg = _interpret_exit_code(128 + 5)
        assert "signal 5" in msg

    def test_low_unknown_code_returns_empty(self) -> None:
        assert _interpret_exit_code(50) == ""

    def test_boundary_128_is_in_table(self) -> None:
        # 128 itself is mapped to "invalid exit argument"; ensure we hit
        # the table branch (with " — ") rather than the signal branch.
        msg = _interpret_exit_code(128)
        assert "invalid exit" in msg


# ── _extract_interactive_output additional paths ───────────────────────


class TestExtractInteractiveOutputEdges:
    def test_screen_equal_to_baseline_returns_stripped_screen(self) -> None:
        baseline = "old\n[DCPTN:0:/workspace] "
        result = _extract_interactive_output(baseline, baseline)
        # Marker is in baseline but content after it is empty; the helper
        # falls back to returning the stripped screen.
        assert result == baseline.strip()

    def test_baseline_without_marker_returns_only_new_lines(self) -> None:
        baseline = "alpha\nbeta"
        screen = "alpha\nbeta\ngamma\ndelta"
        result = _extract_interactive_output(screen, baseline)
        # Order from screen is preserved; only-in-screen lines remain.
        assert result.splitlines() == ["gamma", "delta"]

    def test_marker_in_baseline_returns_only_post_marker_content(self) -> None:
        baseline = "preamble\n[DCPTN:0:/workspace] "
        screen = "preamble\n[DCPTN:0:/workspace] msfconsole\nbanner\nmsf6 > "
        result = _extract_interactive_output(screen, baseline)
        # Pre-marker content must not appear in interactive output.
        assert "preamble" not in result
        assert "msf6 >" in result
        assert "msfconsole" in result


# ── _initialized / _forget_cached_state bookkeeping ────────────────────


class TestBookkeeping:
    @pytest.fixture(autouse=True)
    def _isolate_class_state(self) -> Any:
        # Snapshot + restore the shared class-level set so tests cannot
        # leak state into siblings.
        snapshot = set(TmuxSessionManager._initialized)
        yield
        TmuxSessionManager._initialized.clear()
        TmuxSessionManager._initialized.update(snapshot)

    def test_forget_cached_state_clears_pane_id(self) -> None:
        mgr = TmuxSessionManager("sess-a", "ctn")
        mgr._pane_id = "%42"
        TmuxSessionManager._initialized.add("sess-a")

        mgr._forget_cached_state()

        assert mgr._pane_id is None
        assert "sess-a" not in TmuxSessionManager._initialized

    def test_forget_cached_state_only_removes_own_session(self) -> None:
        TmuxSessionManager._initialized.update({"sess-a", "sess-b"})
        mgr = TmuxSessionManager("sess-a", "ctn")

        mgr._forget_cached_state()

        assert "sess-a" not in TmuxSessionManager._initialized
        assert "sess-b" in TmuxSessionManager._initialized

    def test_forget_cached_state_is_idempotent(self) -> None:
        mgr = TmuxSessionManager("never-init", "ctn")
        # Session was never registered — discard must not raise.
        mgr._forget_cached_state()
        mgr._forget_cached_state()
        assert mgr._pane_id is None

    def test_initialize_skips_when_cached_alive(self) -> None:
        mgr = TmuxSessionManager("cached", "ctn")
        TmuxSessionManager._initialized.add("cached")
        mgr._pane_id = "%7"

        with (
            patch.object(mgr, "_tmux", return_value="%7\n") as mock_tmux,
            patch.object(mgr, "_inject_ps1_marker") as mock_inject,
            patch.object(mgr, "_ensure_session") as mock_ensure,
            patch.object(mgr, "_sync_passthrough_env") as mock_sync,
        ):
            mgr.initialize()

        # Fast path: probe pane only — skip create, PS1 injection, AND env
        # re-sync (the re-sync export's PS1 marker races the user command's
        # marker; env persists from session creation).
        mock_ensure.assert_not_called()
        mock_inject.assert_not_called()
        mock_sync.assert_not_called()
        # display-message probe must have been issued at least once.
        assert any(c.args[0][0] == "display-message" for c in mock_tmux.call_args_list)

    def test_initialize_adds_session_to_initialized_set(self) -> None:
        mgr = TmuxSessionManager("fresh", "ctn")
        TmuxSessionManager._initialized.discard("fresh")

        with (
            patch.object(mgr, "_ensure_session", return_value=False) as mock_ensure,
            patch.object(mgr, "_inject_ps1_marker") as mock_inject,
            patch.object(mgr, "_sync_passthrough_env"),
            patch.object(mgr, "_cached_pane_is_alive", return_value=False),
        ):
            mgr.initialize()

        mock_ensure.assert_called_once()
        mock_inject.assert_called_once()
        assert "fresh" in TmuxSessionManager._initialized

    def test_cached_pane_is_alive_false_when_not_in_initialized(self) -> None:
        mgr = TmuxSessionManager("not-tracked", "ctn")
        TmuxSessionManager._initialized.discard("not-tracked")

        # No _tmux call should be needed — the membership check short-circuits.
        with patch.object(mgr, "_tmux") as mock_tmux:
            assert mgr._cached_pane_is_alive() is False
            mock_tmux.assert_not_called()


# ── manager methods with _tmux/_capture mocked ─────────────────────────


class TestSendAndClear:
    def test_send_uses_literal_mode_and_appends_enter(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        with patch.object(mgr, "_tmux", return_value="") as mock_tmux:
            mgr._send("echo hi", enter=True)

        calls = [c.args[0] for c in mock_tmux.call_args_list]
        # First call sends literal text with -l, second presses Enter.
        assert calls[0] == ["send-keys", "-t", "s", "-l", "echo hi"]
        assert calls[1] == ["send-keys", "-t", "s", "Enter"]

    def test_send_without_enter_does_not_press_enter(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        with patch.object(mgr, "_tmux", return_value="") as mock_tmux:
            mgr._send("partial", enter=False)
        calls = [c.args[0] for c in mock_tmux.call_args_list]
        assert len(calls) == 1
        assert calls[0] == ["send-keys", "-t", "s", "-l", "partial"]

    def test_clear_screen_sends_ctrl_l_then_clear_history(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        with (
            patch.object(mgr, "_tmux", return_value="") as mock_tmux,
            patch("time.sleep"),
        ):
            mgr._clear_screen()

        ops = [c.args[0] for c in mock_tmux.call_args_list]
        assert ops[0] == ["send-keys", "-t", "s", "C-l"]
        assert ops[1] == ["clear-history", "-t", "s"]


class TestReadScreen:
    def test_idle_branch_when_native_command_completed(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        screen = "earlier output\n[DCPTN:0:/workspace] "
        mgr._generation = "a" * 32
        mgr._shell_pid = "123"
        with (
            patch.object(mgr, "_tmux", return_value=f"123\t{'a' * 32}:123:0\t/workspace"),
            patch.object(mgr, "initialize"),
            patch.object(mgr, "_capture", return_value=screen),
        ):
            out = mgr.read_screen()

        assert out.startswith("[IDLE]")
        assert "exit_code=0" in out
        assert "cwd=/workspace" in out

    def test_running_branch_when_content_after_marker(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        screen = "[DCPTN:0:/workspace] tail -f log\nstreaming line"
        mgr._generation = "a" * 32
        mgr._shell_pid = "123"
        with (
            patch.object(mgr, "_tmux", return_value="123\t\t/workspace"),
            patch.object(mgr, "initialize"),
            patch.object(mgr, "_capture", return_value=screen),
        ):
            out = mgr.read_screen()

        assert out.startswith("[RUNNING]")
        assert "streaming line" in out

    def test_unknown_branch_when_no_marker(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        screen = "no marker here at all"
        with (
            patch.object(mgr, "initialize"),
            patch.object(mgr, "_capture", return_value=screen),
        ):
            out = mgr.read_screen()

        assert out.startswith("[UNKNOWN]")
        assert "no marker here" in out

    def test_capture_failure_returns_error_string(self) -> None:
        mgr = TmuxSessionManager("s", "ctn")
        with (
            patch.object(mgr, "initialize"),
            patch.object(mgr, "_capture", side_effect=RuntimeError("boom")),
        ):
            out = mgr.read_screen()

        assert out.startswith("[ERROR]")
        assert "boom" in out
        # Error hint mentions the session so the agent can remediate.
        assert 'bash_kill(session="s")' in out


@pytest.mark.parametrize(
    "native_state",
    [
        "",
        "123\t\t/workspace",
        f"123\t{'b' * 32}:123:0\t/workspace",
        f"123\t{'a' * 32}:456:0\t/workspace",
        f"456\t{'a' * 32}:456:0\t/workspace",
        f"123\t{'a' * 32}:123:999\t/workspace",
    ],
)
def test_completion_rejects_missing_stale_or_foreign_native_state(native_state: str) -> None:
    mgr = TmuxSessionManager("s", "ctn")
    mgr._generation = "a" * 32
    mgr._shell_pid = "123"
    with patch.object(mgr, "_tmux", return_value=native_state):
        assert mgr._command_completion(mgr._generation) is None


def test_read_screen_does_not_credit_printed_prompt() -> None:
    mgr = TmuxSessionManager("s", "ctn")
    with (
        patch.object(mgr, "initialize"),
        patch.object(mgr, "_capture", return_value="[DCPTN:0:/printed]"),
    ):
        assert mgr.read_screen().startswith("[UNKNOWN]")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("record", ["", "stale", "foreign"])
def test_waiter_rejects_printed_completion_without_owned_native_state(
    asynchronous: bool, record: str
) -> None:
    import asyncio
    import itertools

    mgr = TmuxSessionManager("s", "ctn")
    captured = 0

    def boundary(args: list[str], timeout: int = 10) -> str:
        nonlocal captured
        if args[0] == "capture-pane":
            captured += 1
            return "[DCPTN:0:/workspace] " + (
                "" if captured == 1 else "\n[DCPTN:0:/printed]\n[DCPTN_COMPLETE exit=0]"
            )
        if args[0] != "display-message":
            return ""
        if args[-1] == "#{pane_pid}":
            return "123"
        native = ""
        if record == "stale":
            native = f"{'b' * 32}:123:0"
        elif record == "foreign":
            native = f"{mgr._generation}:456:0"
        return f"123\t{native}\t/workspace"

    with (
        patch.object(mgr, "initialize"),
        patch.object(mgr, "_tmux", side_effect=boundary),
        patch("decepticon.sandbox_kernel.tmux.POLL_INTERVAL", 0.0),
        patch("decepticon.sandbox_kernel.tmux.time.monotonic", side_effect=itertools.count(0, 0.1)),
    ):
        output = (
            asyncio.run(mgr.execute_async("printf fixture", False, 1))
            if asynchronous
            else mgr.execute("printf fixture", False, 1)
        )
    assert "[TIMEOUT]" in output
    assert output.endswith("[DCPTN_STATUS incomplete]")
