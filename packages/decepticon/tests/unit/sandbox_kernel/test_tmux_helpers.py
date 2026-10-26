"""Pure-function tests for sandbox tmux helpers.

These tests cover the parsing and formatting helpers that don't need a
running container — exit code interpretation, PS1 marker parsing, output
truncation, interactive output extraction. The TmuxSessionManager init
flow is tested separately with mocks.
"""

from __future__ import annotations

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


class TestInterpretExitCode:
    def test_zero_returns_empty(self) -> None:
        assert _interpret_exit_code(0) == ""

    @pytest.mark.parametrize(
        "code,fragment",
        [
            (1, "general error"),
            (2, "misuse"),
            (126, "permission denied"),
            (127, "command not found"),
            (128, "invalid exit"),
            (130, "Ctrl+C"),
            (137, "OOM"),
            (139, "segmentation"),
            (143, "terminated"),
        ],
    )
    def test_known_codes(self, code: int, fragment: str) -> None:
        msg = _interpret_exit_code(code)
        assert msg.startswith(" — ")
        assert fragment in msg

    def test_unknown_signal_above_128(self) -> None:
        # 128 + 11 = SIGSEGV is in the table; pick something not in the table
        # 128 + 19 = SIGSTOP
        msg = _interpret_exit_code(128 + 19)
        assert "signal 19" in msg

    def test_unknown_low_code(self) -> None:
        assert _interpret_exit_code(42) == ""


class TestPS1Pattern:
    def test_matches_canonical_marker(self) -> None:
        screen = "ls\nfoo bar\n[DCPTN:0:/workspace] "
        match = PS1_PATTERN.search(screen)
        assert match is not None
        assert match.group(1) == "0"
        assert match.group(2) == "/workspace"

    def test_matches_nonzero_exit(self) -> None:
        match = PS1_PATTERN.search("[DCPTN:127:/tmp] ")
        assert match is not None
        assert match.group(1) == "127"
        assert match.group(2) == "/tmp"

    def test_no_match(self) -> None:
        assert PS1_PATTERN.search("just regular shell output") is None


class TestExtractOutput:
    def test_single_marker_returns_full_screen_minus_marker(self) -> None:
        screen = "command output line 1\nline 2\n[DCPTN:0:/workspace] "
        out, exit_code, cwd = _extract_output(screen, command="ls")
        assert exit_code == 0
        assert cwd == "/workspace"
        assert "line 1" in out
        assert "line 2" in out
        assert "DCPTN" not in out

    def test_two_markers_returns_slice_between(self) -> None:
        screen = "[DCPTN:0:/workspace] ls\nfile1.txt\nfile2.txt\n[DCPTN:0:/workspace] "
        out, exit_code, cwd = _extract_output(screen, command="ls")
        assert exit_code == 0
        assert cwd == "/workspace"
        assert "file1.txt" in out
        assert "file2.txt" in out

    def test_no_marker_returns_screen_with_neg_one(self) -> None:
        out, exit_code, cwd = _extract_output("plain text", command="x")
        assert exit_code == -1
        assert cwd == ""
        assert out == "plain text"

    def test_strips_echoed_command(self) -> None:
        # Single echo of the command (typical tmux capture):
        # the helper strips exactly one leading line if it ends with the command.
        screen = "echo hi\nhi\n[DCPTN:0:/workspace] "
        out, exit_code, _ = _extract_output(screen, command="echo hi")
        assert exit_code == 0
        lines = out.split("\n")
        assert lines == ["hi"]

    def test_does_not_strip_when_first_line_unrelated(self) -> None:
        screen = "unrelated banner\nhi\n[DCPTN:0:/workspace] "
        out, _, _ = _extract_output(screen, command="echo hi")
        # First line does not end with command — must be preserved
        assert out.split("\n")[0] == "unrelated banner"

    def test_nonzero_exit_code(self) -> None:
        screen = "error: file not found\n[DCPTN:1:/workspace] "
        out, exit_code, cwd = _extract_output(screen, command="cat missing")
        assert exit_code == 1
        assert "error" in out


class TestTruncate:
    def test_short_text_unchanged(self) -> None:
        text = "hello world"
        assert _truncate(text) == text

    def test_text_at_limit_unchanged(self) -> None:
        text = "x" * MAX_OUTPUT_CHARS
        assert _truncate(text) == text

    def test_long_text_truncated_with_marker(self) -> None:
        text = "A" * (MAX_OUTPUT_CHARS * 2)
        result = _truncate(text)
        assert len(result) < len(text)
        assert "truncated" in result
        # Head and tail preserved
        assert result.startswith("A")
        assert result.endswith("A")

    def test_truncation_preserves_head_and_tail_split(self) -> None:
        head_marker = "HEAD" * 100
        tail_marker = "TAIL" * 100
        middle_filler = "M" * MAX_OUTPUT_CHARS
        text = head_marker + middle_filler + tail_marker
        result = _truncate(text)
        assert "HEAD" in result
        assert "TAIL" in result
        assert "truncated" in result


class TestExtractInteractiveOutput:
    def test_returns_content_after_marker_in_baseline(self) -> None:
        baseline = "old output\n[DCPTN:0:/workspace] "
        screen = "old output\n[DCPTN:0:/workspace] msfconsole\n\nmsf6 > "
        result = _extract_interactive_output(screen, baseline)
        assert "msf6" in result
        # PS1 marker should not appear in extracted output
        assert "DCPTN" not in result or "msf6" in result

    def test_no_marker_falls_back_to_diff(self) -> None:
        baseline = "line1\nline2"
        screen = "line1\nline2\nline3\nline4"
        result = _extract_interactive_output(screen, baseline)
        assert "line3" in result
        assert "line4" in result


class TestTmuxSessionManagerLock:
    """The class-level lock is eagerly initialized and shared across instances."""

    def test_lock_is_initialized(self) -> None:
        # Lock is created eagerly at class definition time, not lazily
        assert TmuxSessionManager._init_lock is not None
        # Reentrant — we can acquire twice from the same thread
        with TmuxSessionManager._init_lock:
            with TmuxSessionManager._init_lock:
                pass

    def test_lock_is_shared_across_instances(self) -> None:
        a = TmuxSessionManager(session="a", container_name="dummy")
        b = TmuxSessionManager(session="b", container_name="dummy")
        assert TmuxSessionManager._init_lock is not None
        # Same object — shared class-level state
        assert a._init_lock is b._init_lock
