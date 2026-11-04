"""``DECEPTICON_RECURSION_LIMIT`` — the operator-tunable step budget.

Issue #1145: an operator rebuilt from source after editing the constant and
saw no effect. The env var flows through the launcher's ``env_file`` so the
budget is tunable without touching code.
"""

from __future__ import annotations

import importlib

import pytest


def test_env_override_values(monkeypatch: pytest.MonkeyPatch) -> None:
    import decepticon.agents.standard.decepticon as decepticon_module

    monkeypatch.setenv("DECEPTICON_RECURSION_LIMIT", "600")
    assert decepticon_module._recursion_limit_from_env() == 600

    monkeypatch.setenv("DECEPTICON_RECURSION_LIMIT", "10")
    assert decepticon_module._recursion_limit_from_env() == 25

    monkeypatch.setenv("DECEPTICON_RECURSION_LIMIT", "not-a-number")
    assert decepticon_module._recursion_limit_from_env() == 400

    monkeypatch.delenv("DECEPTICON_RECURSION_LIMIT", raising=False)
    assert decepticon_module._recursion_limit_from_env() == 400


def test_module_constant_reflects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import decepticon.agents.standard.decepticon as decepticon_module

    monkeypatch.setenv("DECEPTICON_RECURSION_LIMIT", "555")
    importlib.reload(decepticon_module)
    try:
        assert decepticon_module._RECURSION_LIMIT == 555
    finally:
        monkeypatch.delenv("DECEPTICON_RECURSION_LIMIT", raising=False)
        importlib.reload(decepticon_module)
    assert decepticon_module._RECURSION_LIMIT == 400
