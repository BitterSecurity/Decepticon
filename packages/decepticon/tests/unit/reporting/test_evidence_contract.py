from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from decepticon.tools.reporting.evidence_contract import (
    evidence_path,
    validate_evidence_metadata,
    validate_finding_document,
)


def artifact(root: Path, path: str, content: str = "captured output") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


def test_manifest_and_legacy_and_step_references(tmp_path: Path) -> None:
    shared = "findings/evidence/FIND-001.txt"
    capture = "evidence/recordings/session.cast"
    artifact(tmp_path, shared)
    artifact(tmp_path, capture)
    artifact(
        tmp_path,
        "findings/FIND-001.md",
        f"""---
id: FIND-001
evidence_pointer: {shared}
evidence_manifest:
  - path: {shared}
    label: Request log
    role: observation
    supports: The response status was 403
attack_steps:
  - Legacy narrative
  - label: Observe request
    status: observed
    evidence_refs: [{shared}, {capture}]
---
# Finding
""",
    )
    result = validate_finding_document(tmp_path, "findings/FIND-001.md")
    assert result.valid, result.errors
    assert len(result.references) == 2
    assert result.references[0].label == "Request log"
    assert result.references[0].role == "observation"
    assert result.references[0].supports == "The response status was 403"
    assert result.references[1].path == capture


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        "/etc/passwd",
        "findings/evidence/../private.txt",
        "findings/evidence//log.txt",
        "findings/evidence/./log.txt",
        "findings/evidence/%2e%2e/file",
        "findings/evidence/file?query",
        "findings/evidence/file#fragment",
        "findings/evidence/back\\slash",
        "findings/evidence/has space",
        "findings/evidence/x\x00",
        "findings/evidence/x\x7f",
        "findings/evidence/C:file",
        "plan/secret.txt",
        "findings/evidence/",
        "x" * 1025,
        None,
        1,
    ],
)
def test_reject_noncanonical_reference(path: object) -> None:
    with pytest.raises(ValueError):
        evidence_path(path)


@pytest.mark.parametrize(
    "directory", ["recon/evidence", "exploit/evidence", "findings/evidence", "evidence"]
)
def test_supported_evidence_roots(tmp_path: Path, directory: str) -> None:
    path = f"{directory}/output.jsonl"
    artifact(tmp_path, path)
    assert validate_evidence_metadata(tmp_path, {"evidence_pointer": path}).valid


@pytest.mark.parametrize("external", [False, True])
def test_symlink_file_rejected_even_within_root(tmp_path: Path, external: bool) -> None:
    destination = artifact(tmp_path, "actual.txt")
    if external:
        destination = Path("/dev/null")
    link = tmp_path / "findings/evidence/link.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(destination)
    result = validate_evidence_metadata(
        tmp_path, {"evidence_pointer": "findings/evidence/link.txt"}
    )
    assert not result.valid
    assert "symlinked" in result.errors[0]


def test_symlink_directory_rejected(tmp_path: Path) -> None:
    artifact(tmp_path, "real/log.txt")
    (tmp_path / "findings").mkdir()
    (tmp_path / "findings/evidence").symlink_to(tmp_path / "real", target_is_directory=True)
    assert not validate_evidence_metadata(
        tmp_path, {"evidence_pointer": "findings/evidence/log.txt"}
    ).valid


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo"])
def test_non_file_rejected_without_blocking(tmp_path: Path, kind: str) -> None:
    target = tmp_path / "evidence/log"
    target.parent.mkdir()
    if kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    assert not validate_evidence_metadata(tmp_path, {"evidence_pointer": "evidence/log"}).valid


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"evidence_manifest": "bad"},
        {"evidence_manifest": [None]},
        {"evidence_manifest": [{}] * 101},
        {"attack_steps": [{}] * 31},
        {"attack_steps": [None]},
        {"attack_steps": [{"label": "step", "status": "confirmed"}]},
        {"attack_steps": [{"label": "step", "status": "observed", "evidence_refs": "bad"}]},
        {"evidence_pointer": None},
        {"evidence_manifest": [{"path": "evidence/a", "label": 7}]},
    ],
)
def test_malformed_metadata_reports_errors(tmp_path: Path, metadata: object) -> None:
    assert not validate_evidence_metadata(tmp_path, metadata).valid


def test_duplicate_manifest_not_silently_overwritten(tmp_path: Path) -> None:
    artifact(tmp_path, "evidence/a")
    result = validate_evidence_metadata(
        tmp_path,
        {
            "evidence_manifest": [{"path": "evidence/a"}, {"path": "evidence/a"}],
        },
    )
    assert not result.valid
    assert "duplicate" in result.errors[0]


@pytest.mark.parametrize(
    "content",
    [
        "---\n",
        "---\nx: [\n---",
        "---\nx: 1\nx: 2\n---",
        "---\n- item\n---",
        "a" * (1024 * 1024 + 1),
    ],
)
def test_invalid_document(tmp_path: Path, content: str) -> None:
    artifact(tmp_path, "report/summary.md", content)
    assert not validate_finding_document(tmp_path, "report/summary.md").valid


def test_legacy_report_without_metadata_is_readable(tmp_path: Path) -> None:
    artifact(tmp_path, "report/summary.md", "# Summary\nLegacy content")
    result = validate_finding_document(tmp_path, "report/summary.md")
    assert result.valid
    assert result.references == ()


def test_document_symlink_rejected(tmp_path: Path) -> None:
    real = artifact(tmp_path, "real.md", "# Summary")
    (tmp_path / "report").mkdir()
    (tmp_path / "report/summary.md").symlink_to(real)
    assert not validate_finding_document(tmp_path, "report/summary.md").valid


def test_cli_exit_and_json(tmp_path: Path) -> None:
    artifact(tmp_path, "report/summary.md", "---\nevidence_pointer: evidence/a\n---\nSummary")
    command = [
        sys.executable,
        "-m",
        "decepticon.tools.reporting.evidence_contract",
        "--root",
        str(tmp_path),
        "report/summary.md",
    ]
    # This standalone validator does not need Decepticon's agent registry boot.
    environment = {**os.environ, "DECEPTICON_SKIP_BOOT": "1"}
    missing = subprocess.run(command, capture_output=True, text=True, timeout=30, env=environment)
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["valid"] is False
    artifact(tmp_path, "evidence/a")
    available = subprocess.run(command, capture_output=True, text=True, timeout=30, env=environment)
    assert available.returncode == 0, available.stderr
    assert json.loads(available.stdout)["references"][0]["path"] == "evidence/a"
