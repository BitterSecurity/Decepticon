"""Per-test isolation for process-wide sandbox class state.

``SandboxBase._log_offsets`` and ``_jobs`` are deliberately class-level —
shared by every agent factory in a process (see ``SandboxBase``'s docstring).
That contract is correct for production but leaks across tests: one test's
``read_session_log_diff`` call populates ``_log_offsets``, and a later test
that asserts a pristine offset map then fails. Under ``pytest -n auto`` the
victim test varies with execution order. Resetting the state around every
test makes each one start from the documented clean baseline.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from decepticon_sandbox.kernel import BackgroundJobTracker, TmuxSessionManager
from decepticon_sandbox.kernel.base import SandboxBase
from decepticon_sandbox.kernel.daemon import DaemonSandbox


@pytest.fixture(autouse=True)
def _reset_sandbox_class_state() -> Iterator[None]:
    """Reset shared sandbox ClassVar state before and after each test."""

    def _reset() -> None:
        for cls in (SandboxBase, DaemonSandbox):
            cls._log_offsets.clear()
            cls._jobs = BackgroundJobTracker()
        TmuxSessionManager._initialized.clear()

    _reset()
    yield
    _reset()
