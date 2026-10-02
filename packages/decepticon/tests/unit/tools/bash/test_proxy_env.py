"""Sandbox tmux-session env passthrough — proxy + DECEPTICON_* allowlist.

The bash tool routes commands through tmux shells managed by
``TmuxSessionManager``. For traffic-intercept workflows (Caido / Burp /
ZAP) the shell must inherit ``HTTPS_PROXY`` and friends from the
orchestrator process; otherwise commands like ``curl``, ``requests``, or
any HTTP client bypass the proxy and traffic is lost. These tests pin
the allowlist semantics and verify the sync is wired through the
``initialize()`` lifecycle.
"""

from __future__ import annotations

import os
import shlex
from unittest.mock import patch

import pytest

from decepticon.sandbox_kernel import tmux as tmux_module
from decepticon.sandbox_kernel.tmux import (
    TmuxSessionManager,
    _allowed_passthrough_env,
    _shell_export_command,
)

_WINDOWS = os.name == "nt"

_PROXY_NAMES_UPPER = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY")
_PROXY_NAMES_LOWER = ("https_proxy", "http_proxy", "no_proxy", "all_proxy")


def _clear_proxy_and_decepticon_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (*_PROXY_NAMES_UPPER, *_PROXY_NAMES_LOWER):
        monkeypatch.delenv(key, raising=False)
    for key in list(os.environ):
        if key.startswith("DECEPTICON_"):
            monkeypatch.delenv(key, raising=False)


def test_allowed_passthrough_env_includes_uppercase_proxy_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.example:1080")

    env = _allowed_passthrough_env()

    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert env["HTTP_PROXY"] == "http://proxy.example:8080"
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["ALL_PROXY"] == "socks5://proxy.example:1080"


@pytest.mark.skipif(
    _WINDOWS,
    reason="Windows env vars are case-insensitive; lowercase variants tested implicitly via uppercase",
)
def test_allowed_passthrough_env_includes_lowercase_proxy_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://lower.example:8080")
    monkeypatch.setenv("http_proxy", "http://lower.example:8080")
    monkeypatch.setenv("no_proxy", "localhost")
    monkeypatch.setenv("all_proxy", "socks5://lower.example:1080")

    env = _allowed_passthrough_env()

    assert env["https_proxy"] == "http://lower.example:8080"
    assert env["http_proxy"] == "http://lower.example:8080"
    assert env["no_proxy"] == "localhost"
    assert env["all_proxy"] == "socks5://lower.example:1080"


def test_allowed_passthrough_env_includes_decepticon_prefixed_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "eng-1")
    monkeypatch.setenv("DECEPTICON_TEST_TOKEN", "tok-2")

    env = _allowed_passthrough_env()

    assert env["DECEPTICON_ENGAGEMENT"] == "eng-1"
    assert env["DECEPTICON_TEST_TOKEN"] == "tok-2"


def test_allowed_passthrough_env_excludes_unrelated_vars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-forward")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-forward")
    monkeypatch.setenv("DECEPTICON_TOKEN", "ok")

    env = _allowed_passthrough_env()

    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "UNRELATED_SECRET" not in env
    assert "HTTPS_PROXY" in env
    assert "DECEPTICON_TOKEN" in env


def test_allowed_passthrough_env_returns_empty_when_nothing_to_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    assert _allowed_passthrough_env() == {}


def test_shell_export_command_value_round_trips_through_shlex() -> None:
    raw = {
        "HTTPS_PROXY": "http://user:p@ss word@proxy:8080",
        "NO_PROXY": "localhost,127.0.0.1",
        "DECEPTICON_ENGAGEMENT": "eng with spaces",
    }
    cmd = _shell_export_command(raw)

    assert cmd.startswith("export ")
    for key, value in raw.items():
        assert f"{key}={shlex.quote(value)}" in cmd


def test_shell_export_command_is_empty_for_empty_input() -> None:
    assert _shell_export_command({}) == ""


def test_shell_export_command_emits_keys_in_sorted_order() -> None:
    inputs = {"https_proxy": "v1", "HTTPS_PROXY": "v2", "DECEPTICON_X": "v3"}
    cmd = _shell_export_command(inputs)
    expected = "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(inputs.items()))
    assert cmd == expected


def test_shell_export_command_rejects_malformed_keys() -> None:
    cmd = _shell_export_command({"BAD KEY": "v", "HTTPS_PROXY": "ok"})
    assert "BAD KEY" not in cmd
    assert f"HTTPS_PROXY={shlex.quote('ok')}" in cmd


def test_shell_export_command_shell_quotes_injection_attempts() -> None:
    dangerous = "x'; rm -rf /; echo 'pwn"
    cmd = _shell_export_command({"HTTPS_PROXY": dangerous})
    value_part = cmd.split("=", 1)[1]
    assert value_part == shlex.quote(dangerous)


def test_sync_passthrough_env_sends_export_into_tmux_when_allowlist_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    monkeypatch.setenv("DECEPTICON_ENGAGEMENT", "eng-7")

    sent: list[tuple[str, bool]] = []
    mgr = TmuxSessionManager(session="s1", container_name="ctn")
    with (
        patch.object(mgr, "_send", side_effect=lambda text, enter=True: sent.append((text, enter))),
        patch.object(mgr, "_capture", side_effect=["", "[DCPTN:0:/workspace] "]),
    ):
        mgr._sync_passthrough_env()

    assert len(sent) == 1
    text, enter = sent[0]
    assert enter is True
    assert text.startswith("export ")
    assert f"HTTPS_PROXY={shlex.quote('http://proxy:8080')}" in text
    assert f"DECEPTICON_ENGAGEMENT={shlex.quote('eng-7')}" in text


def test_sync_passthrough_env_is_noop_when_nothing_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)

    sent: list[tuple[str, bool]] = []
    mgr = TmuxSessionManager(session="s2", container_name="ctn")
    with patch.object(
        mgr,
        "_send",
        side_effect=lambda text, enter=True: sent.append((text, enter)),
    ):
        mgr._sync_passthrough_env()

    assert sent == []


def test_sync_passthrough_env_swallows_send_errors_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    mgr = TmuxSessionManager(session="s3", container_name="ctn")
    with (
        patch.object(mgr, "_send", side_effect=RuntimeError("tmux gone")),
        patch.object(mgr, "_capture", return_value="[DCPTN:0:/workspace] "),
    ):
        mgr._sync_passthrough_env()


def test_initialize_skips_sync_passthrough_env_when_cached_session_is_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Re-syncing on the cached-alive path emits an export line whose PS1
    # marker races the user command's marker (see tmux.py initialize()).
    # The env was synced at session creation and persists, so the fast
    # path must NOT re-sync.
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    mgr = TmuxSessionManager(session="cached", container_name="ctn")
    sync_calls: list[int] = []
    with (
        patch.object(mgr, "_cached_pane_is_alive", return_value=True),
        patch.object(mgr, "_sync_passthrough_env", side_effect=lambda: sync_calls.append(1)),
    ):
        mgr.initialize()

    assert sync_calls == []


def test_initialize_calls_sync_passthrough_env_after_creating_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_and_decepticon_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

    mgr = TmuxSessionManager(session="fresh", container_name="ctn")
    TmuxSessionManager._initialized.discard("fresh")

    def _fake_tmux(args: list[str], timeout: int = 10) -> str:
        if args[:1] == ["has-session"]:
            raise RuntimeError("no such session")
        if args[:1] == ["new-session"]:
            return "%0"
        return ""

    sync_calls: list[int] = []
    with (
        patch.object(mgr, "_tmux", side_effect=_fake_tmux),
        patch.object(mgr, "_send"),
        patch.object(mgr, "_clear_screen"),
        patch.object(mgr, "_resolve_pane_id", return_value="%0"),
        patch.object(mgr, "_sync_passthrough_env", side_effect=lambda: sync_calls.append(1)),
        patch.object(tmux_module.time, "sleep"),
    ):
        mgr.initialize()

    assert sync_calls == [1]
