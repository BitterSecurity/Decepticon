"""The report checker rejects unsupported status and score claims."""

from __future__ import annotations

import json
from types import SimpleNamespace

from deepagents.backends.filesystem import FilesystemBackend

from decepticon.tools.reporting.finding_quality import (
    build_finding_quality_tool,
    check_finding_content,
)


def finding(**overrides):
    meta = {
        "id": "FIND-001",
        "result_kind": "vulnerability",
        "severity": "high",
        "verification_status": "verified",
        "verification_rationale": "Positive differs from baseline",
        "report_status": "draft",
        "evidence_pointer": "findings/evidence/proof.txt",
        "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "cvss_score": 9.8,
        "cvss_version": "3.1",
    }
    meta.update(overrides)
    front = "\n".join(f"{key}: {value}" for key, value in meta.items() if value is not None)
    return f"---\n{front}\n---\n## Summary\nObserved issue\n## Verification\nPositive and negative\n## Impact\nObserved impact\n## Remediation\nFix\n"


def check(text, exists=lambda _path: True):
    return check_finding_content("findings/FIND-001.md", text, evidence_exists=exists)


def test_valid_saved_finding():
    assert check(finding()) == []


def test_rejects_missing_evidence_and_score_mismatch():
    issues = check(finding(cvss_score=8.8), exists=lambda _path: False)
    assert any("CVSS score" in issue for issue in issues)
    assert any("missing evidence" in issue for issue in issues)


def test_rejects_false_positive_scored_as_vulnerability():
    issues = check(finding(verification_status="false_positive"))
    assert any("false-positive" in issue for issue in issues)


def test_verified_negative_result_is_coverage_without_cvss():
    text = finding(result_kind="negative_result", cvss_vector=None, cvss_score=None)
    assert check(text) == []


def test_rejects_duplicate_frontmatter_key():
    text = finding().replace("id: FIND-001", "id: FIND-001\nid: FIND-002", 1)
    assert any("frontmatter" in issue for issue in check(text))


def test_agent_tool_reads_only_active_workspace(tmp_path):
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    backend.write("/workspace/findings/FIND-001.md", finding())
    backend.write("/workspace/findings/evidence/proof.txt", "positive and baseline")
    tool = build_finding_quality_tool(backend)
    runtime = SimpleNamespace(state={"workspace_path": "/workspace"})
    result = json.loads(tool.func("findings/FIND-001.md", runtime))
    assert result == {"valid": True, "errors": []}
    blocked = json.loads(tool.func("../FIND-001.md", runtime))
    assert blocked["valid"] is False
