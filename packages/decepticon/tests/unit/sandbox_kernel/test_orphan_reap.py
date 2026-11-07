"""Startup orphan-reap for tmux sessions left by a previously-killed daemon.

If the daemon is SIGKILLed, its tmux sessions/sockets survive but the new
process starts with an empty manager map — they would leak forever. The
reap path discovers pre-existing decepticon-named sockets on the host and
kills the ones not tracked by the live manager.

These tests cover the pure decision function (no I/O) and the reap method
with the discovery/kill seams mocked, so they pass in CI where tmux is not
installed.
"""

from __future__ import annotations

import pytest

from decepticon.sandbox_kernel.core import _compute_orphan_tmux_sessions
from decepticon.sandbox_kernel.daemon import DaemonSandbox

# ── pure decision function ─────────────────────────────────────────────


def test_compute_orphans_returns_discovered_minus_tracked() -> None:
    discovered = {"dcptn_eng-abc_main", "dcptn_eng-abc_recon", "dcptn_eng-xyz_main"}
    tracked = {"dcptn_eng-abc_main"}

    assert _compute_orphan_tmux_sessions(discovered, tracked) == {
        "dcptn_eng-abc_recon",
        "dcptn_eng-xyz_main",
    }


def test_compute_orphans_spares_every_tracked_session() -> None:
    discovered = {"dcptn_eng-abc_main", "dcptn_eng-abc_recon"}
    tracked = {"dcptn_eng-abc_main", "dcptn_eng-abc_recon"}

    assert _compute_orphan_tmux_sessions(discovered, tracked) == set()


def test_compute_orphans_ignores_non_decepticon_sockets() -> None:
    # The reap path must scope to decepticon-named sockets only — never
    # touch a session that some other process (e.g. an operator's shell)
    # might own on a shared host.
    discovered = {"dcptn_eng-abc_main", "user-tmux", "main", "agent-session-1"}
    tracked: set[str] = set()

    assert _compute_orphan_tmux_sessions(discovered, tracked) == {"dcptn_eng-abc_main"}


def test_compute_orphans_with_empty_discovered_is_empty() -> None:
    assert _compute_orphan_tmux_sessions(set(), {"dcptn_eng-abc_main"}) == set()


# ── reap method (seams mocked) ─────────────────────────────────────────


def test_reap_kills_only_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    sandbox = DaemonSandbox()
    # The session name includes a workspace-slug digest (see
    # SandboxBase._workspace_slug) — bind it to the actual live manager
    # so the test is robust to slug-generation changes.
    live_mgr = sandbox._get_manager("main", "/workspace/eng-abc")
    live_session = live_mgr.session
    assert live_session.startswith("dcptn_")

    discovered = [live_session, "dcptn_eng-abc_recon", "dcptn_eng-xyz_main"]
    killed: list[str] = []

    monkeypatch.setattr(
        "decepticon.sandbox_kernel.core._list_decepticon_tmux_sockets",
        lambda: list(discovered),
    )
    monkeypatch.setattr(
        "decepticon.sandbox_kernel.core._kill_tmux_socket",
        lambda s: killed.append(s),
    )

    reaped = sandbox.reap_orphaned_tmux_sessions()

    assert sorted(killed) == ["dcptn_eng-abc_recon", "dcptn_eng-xyz_main"]
    assert reaped == 2


def test_reap_is_a_noop_when_nothing_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[str] = []

    monkeypatch.setattr("decepticon.sandbox_kernel.core._list_decepticon_tmux_sockets", lambda: [])
    monkeypatch.setattr(
        "decepticon.sandbox_kernel.core._kill_tmux_socket",
        lambda s: killed.append(s),
    )

    assert DaemonSandbox().reap_orphaned_tmux_sessions() == 0
    assert killed == []


def test_reap_swallows_kill_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    # One stuck socket must not block the rest from being reaped.
    monkeypatch.setattr(
        "decepticon.sandbox_kernel.core._list_decepticon_tmux_sockets",
        lambda: ["dcptn_eng-abc_main", "dcptn_eng-abc_recon"],
    )

    killed: list[str] = []

    def flaky_kill(session: str) -> None:
        if session == "dcptn_eng-abc_main":
            raise RuntimeError("simulated tmux failure")
        killed.append(session)

    monkeypatch.setattr("decepticon.sandbox_kernel.core._kill_tmux_socket", flaky_kill)

    reaped = DaemonSandbox().reap_orphaned_tmux_sessions()
    # The successful kill is counted; the failure is logged + swallowed.
    assert killed == ["dcptn_eng-abc_recon"]
    assert reaped == 1
