"""Regression: the poll loop must fail fast after repeated transient capture
failures instead of resetting the stall timer forever.

A wedged ``docker exec`` (``OSError`` / ``subprocess.TimeoutExpired`` raised by
``_capture``) previously reset ``last_change_time`` every iteration, so the
command span until the full ``timeout`` elapsed -- pinning a core the whole
time. Both the sync and async poll loops now give up after
``MAX_CONSECUTIVE_CAPTURE_FAILURES`` consecutive failures and return a distinct
infrastructure error.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

import pytest

from decepticon.sandbox_kernel import tmux as tmux_mod
from decepticon.sandbox_kernel.tmux import TmuxSessionManager

_EXCEPTIONS = [
    OSError("docker exec stalled"),
    subprocess.TimeoutExpired(cmd="tmux", timeout=10),
]


def _stalling_capture(exc: BaseException) -> tuple[Any, dict[str, int]]:
    state = {"n": 0}

    def capture() -> str:
        state["n"] += 1
        if state["n"] == 1:
            return "baseline-no-marker"
        raise exc

    return capture, state


def _make_manager(monkeypatch: pytest.MonkeyPatch, capture: Any) -> TmuxSessionManager:
    mgr = TmuxSessionManager(session="t", container_name="c")
    monkeypatch.setattr(mgr, "initialize", lambda: None)
    monkeypatch.setattr(mgr, "_send", lambda *a, **k: None)
    monkeypatch.setattr(mgr, "_forget_cached_state", lambda: None)
    monkeypatch.setattr(mgr, "_capture", capture)
    monkeypatch.setattr(mgr, "_tmux", lambda *a, **k: "123")
    monkeypatch.setattr(tmux_mod, "POLL_INTERVAL", 0.0)
    return mgr


@pytest.mark.parametrize("exc", _EXCEPTIONS)
def test_sync_poll_caps_consecutive_capture_failures(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    capture, state = _stalling_capture(exc)
    mgr = _make_manager(monkeypatch, capture)

    result = mgr.execute("sleep 999", is_input=False, timeout=2)

    assert "[ERROR]" in result
    assert "capture failed" in result.lower()
    assert state["n"] <= tmux_mod.MAX_CONSECUTIVE_CAPTURE_FAILURES + 2


@pytest.mark.parametrize("exc", _EXCEPTIONS)
def test_async_poll_caps_consecutive_capture_failures(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    capture, state = _stalling_capture(exc)
    mgr = _make_manager(monkeypatch, capture)

    result = asyncio.run(mgr.execute_async("sleep 999", is_input=False, timeout=2))

    assert "[ERROR]" in result
    assert "capture failed" in result.lower()
    assert state["n"] <= tmux_mod.MAX_CONSECUTIVE_CAPTURE_FAILURES + 2
