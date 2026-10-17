"""Operator provisioning must repair a missing RoE without granting execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _invoke(workspace: Path, source: Path, *flags: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "decepticon.provision_roe", "--workspace", str(workspace),
         "--file", str(source), *flags],
        capture_output=True, text=True, check=False,
        env={**os.environ, "DECEPTICON_SKIP_BOOT": "1"},
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "plan").mkdir()
    (tmp_path / "plan" / "conops.json").write_bytes(b'{"existing":"plan"}')
    return tmp_path


@pytest.fixture
def source(tmp_path: Path) -> Path:
    source = tmp_path / "authorized.json"
    source.write_text(json.dumps({
        "engagement_name": "Existing engagement", "client": "Local operator",
        "start_date": "2026-10-17", "end_date": "2026-10-17",
        "engagement_type": "external", "testing_window": "09:00-17:00 UTC",
        "in_scope": [{"target": "127.0.0.1", "type": "ip-range"}],
        "authorization_reference": "Local fixture authorization",
        "machine_enforcement": {"mode": "enforce", "in_scope": ["127.0.0.1"]},
    }))
    return source


def test_provisions_missing_roe_preserving_plans_without_approval(workspace: Path, source: Path) -> None:
    # Given an existing plan and an operator-reviewed RoE.
    before = (workspace / "plan" / "conops.json").read_bytes()
    # When the operator explicitly confirms provisioning.
    result = _invoke(workspace, source, "--confirm")
    # Then the reviewed bytes are installed without altering plans or approving Red.
    assert result.returncode == 0, result.stderr
    assert (workspace / "plan" / "roe.json").read_bytes() == source.read_bytes()
    assert (workspace / "plan" / "conops.json").read_bytes() == before
    assert not (workspace / ".red-approved").exists()
    assert not (workspace / ".planning-draft-ready").exists()
    assert (workspace / "plan" / "roe.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("change", ["invalid-json", "missing-field", "blank-authorization", "empty-scope"])
def test_rejects_invalid_authorization_without_writing(workspace: Path, source: Path, change: str) -> None:
    # Given an invalid or incomplete authorization document.
    payload = json.loads(source.read_text())
    if change == "missing-field":
        del payload["client"]
    if change == "blank-authorization":
        payload["authorization_reference"] = " "
    if change == "empty-scope":
        payload["in_scope"] = []
    source.write_text("{" if change == "invalid-json" else json.dumps(payload))
    # When provisioning is attempted.
    result = _invoke(workspace, source, "--confirm")
    # Then it fails without creating the protected document.
    assert result.returncode == 2, result.stderr
    assert not (workspace / "plan" / "roe.json").exists()


def test_requires_operator_confirmation(workspace: Path, source: Path) -> None:
    # Given a valid source without operator confirmation.
    # When provisioning is attempted.
    result = _invoke(workspace, source)
    # Then there is no write.
    assert result.returncode == 2, result.stderr
    assert not (workspace / "plan" / "roe.json").exists()


def test_refuses_to_overwrite_existing_roe(workspace: Path, source: Path) -> None:
    # Given an already provisioned RoE.
    destination = workspace / "plan" / "roe.json"
    destination.write_bytes(b"existing authorization")
    # When another import is attempted.
    result = _invoke(workspace, source, "--confirm")
    # Then the existing authorization survives unchanged.
    assert result.returncode == 2, result.stderr
    assert destination.read_bytes() == b"existing authorization"


def test_refuses_symlinked_plan(workspace: Path, source: Path, tmp_path: Path) -> None:
    # Given a plan directory redirected outside the engagement.
    (workspace / "plan" / "conops.json").unlink()
    (workspace / "plan").rmdir()
    external = tmp_path / "external"
    external.mkdir()
    (workspace / "plan").symlink_to(external, target_is_directory=True)
    # When provisioning is attempted.
    result = _invoke(workspace, source, "--confirm")
    # Then it cannot install authorization in the redirected directory.
    assert result.returncode == 2, result.stderr
    assert not (external / "roe.json").exists()
