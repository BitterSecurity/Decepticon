"""Tests for ``strip_terminal_noise`` — the pipe-pane session-log sanitizer.

``read_session_log_diff`` (the ``bash_output`` backing) returns the raw
``pipe-pane`` log, which streams unrendered pane bytes: OSC shell-
integration markers, CSI sequences (colors, bracketed-paste ``?2004h/l``),
the ``[DCPTN:…]`` PS1 markers, and stray control chars. Unlike
``capture-pane`` (which renders to a clean screen), none of that is
stripped — so it leaked to the agent / LangSmith. These pin the cleanup.
"""

from __future__ import annotations

from decepticon_sandbox.kernel.tmux import strip_terminal_noise


def test_strips_osc_3008_shell_integration():
    raw = "\x1b]3008;start=abc;machineid=z;type=shell;cwd=/workspace/x\x1b\\hello\n"
    assert strip_terminal_noise(raw) == "hello\n"


def test_strips_osc_terminated_by_bel():
    raw = "\x1b]0;window title\x07payload\n"
    assert strip_terminal_noise(raw) == "payload\n"


def test_strips_bracketed_paste_toggles():
    raw = "\x1b[?2004hcmd line\x1b[?2004l\n"
    assert strip_terminal_noise(raw) == "cmd line\n"


def test_strips_sgr_color_codes():
    raw = "\x1b[31mred\x1b[0m normal\n"
    assert strip_terminal_noise(raw) == "red normal\n"


def test_strips_ps1_dcptn_markers():
    raw = "[DCPTN:0:/workspace/recon] echo hi\n"
    assert strip_terminal_noise(raw) == " echo hi\n"


def test_normalizes_crlf_and_strips_c0_controls():
    raw = "line1\r\nline2\x00\x07\n"
    assert strip_terminal_noise(raw) == "line1\nline2\n"


def test_keeps_plain_text_newlines_and_tabs():
    raw = "plain\ttext\nsecond line\n"
    assert strip_terminal_noise(raw) == "plain\ttext\nsecond line\n"


def test_idle_marker_passes_through_unchanged():
    raw = "[IDLE] No background job in session 'recon'.\n"
    assert strip_terminal_noise(raw) == raw


def test_empty_input():
    assert strip_terminal_noise("") == ""


def test_realistic_pipe_pane_sample_is_clean():
    raw = (
        "\x1b]3008;start=e2e;machineid=abc;type=shell;cwd=/workspace/recon\x1b\\"
        "\x1b[?2004h[DCPTN:0:/workspace/recon] cd /workspace/recon\x1b[?2004l\n"
        "\x1b[?2004himport subprocess\x1b[?2004l\n"
        "\x1b]3008;start=c9;type=command;cwd=/workspace/recon\x1b\\"
        "[+] Querying crt.sh for semrush.com...\n"
    )
    out = strip_terminal_noise(raw)
    assert "\x1b" not in out  # no escape bytes
    assert "3008" not in out  # OSC shell-integration gone
    assert "2004" not in out  # bracketed-paste gone
    assert "DCPTN" not in out  # PS1 marker gone
    # Real content survives.
    assert "cd /workspace/recon" in out
    assert "import subprocess" in out
    assert "[+] Querying crt.sh for semrush.com..." in out
