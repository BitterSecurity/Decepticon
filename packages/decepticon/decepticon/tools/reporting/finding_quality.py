"""Structural quality check for a canonical finding before reporting completes."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import yaml
from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, tool

from decepticon.middleware.filesystem import EngagementFilesystemBackend
from decepticon.tools.reporting.cvss import CVSSError, base_score, parse_vector
from decepticon.tools.reporting.evidence_contract import _FrontmatterLoader, evidence_path

_FINDING_PATH = re.compile(r"^findings/(FIND-\d{3,})\.md$")
_VERDICTS = {"verified", "false_positive", "unverified"}
_KINDS = {"vulnerability", "negative_result", "observation", "hypothesis"}


def check_finding_content(
    path: str, content: str, *, evidence_exists: Callable[[str], bool]
) -> list[str]:
    """Check machine-verifiable report invariants; never judge exploit truth."""
    errors: list[str] = []
    match = _FINDING_PATH.fullmatch(path)
    if match is None:
        return ["path must be findings/FIND-NNN.md"]
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        return ["finding must have YAML frontmatter"]
    try:
        end = lines.index("---", 1)
        metadata = yaml.load("\n".join(lines[1:end]), Loader=_FrontmatterLoader)
    except (ValueError, yaml.YAMLError) as exc:
        return [f"invalid YAML frontmatter: {exc}"]
    if not isinstance(metadata, dict):
        return ["frontmatter must be a mapping"]
    if metadata.get("id") != match.group(1):
        errors.append("id must match canonical filename")
    kind = metadata.get("result_kind", "vulnerability")
    if kind not in _KINDS:
        errors.append("result_kind is invalid")
    verdict = metadata.get("verification_status")
    if verdict not in _VERDICTS:
        errors.append("verification_status must be verified, false_positive or unverified")
    if (
        not isinstance(metadata.get("verification_rationale"), str)
        or not metadata["verification_rationale"].strip()
    ):
        errors.append("verification_rationale is required")
    if metadata.get("report_status") not in {"draft", "complete"}:
        errors.append("report_status must be draft or complete")
    vector = metadata.get("cvss_vector")
    score = metadata.get("cvss_score")
    if kind != "vulnerability" or verdict == "false_positive":
        if score is not None or vector:
            errors.append("negative/false-positive result must not carry a CVSS score")
    elif score is not None or vector:
        if (
            not isinstance(vector, str)
            or not vector.startswith("CVSS:3.1/")
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
        ):
            errors.append("CVSS 3.1 vector and numeric score must be supplied together")
        else:
            try:
                expected = base_score(parse_vector(vector))
            except CVSSError as exc:
                errors.append(f"invalid CVSS 3.1 vector: {exc}")
            else:
                if score != expected or str(metadata.get("cvss_version")) != "3.1":
                    errors.append("CVSS score/version does not match the CVSS 3.1 vector")
    paths: list[Any] = []
    if "evidence_pointer" in metadata:
        paths.append(metadata["evidence_pointer"])
    manifest = metadata.get("evidence_manifest", [])
    if not isinstance(manifest, list):
        errors.append("evidence_manifest must be a list")
    else:
        paths.extend(item.get("path") if isinstance(item, dict) else None for item in manifest)
    if kind == "vulnerability" and not paths:
        errors.append("vulnerability finding needs a saved evidence reference")
    for raw in paths:
        try:
            evidence = evidence_path(raw)
        except ValueError as exc:
            errors.append(f"evidence reference: {exc}")
            continue
        if not evidence_exists(evidence):
            errors.append(f"missing evidence: {evidence}")
    body = "\n".join(lines[end + 1 :])
    for heading in ("Summary", "Verification"):
        if not re.search(rf"^## {heading}\s*$", body, flags=re.MULTILINE):
            errors.append(f"missing ## {heading} section")
    if verdict == "verified" and kind == "vulnerability":
        for heading in ("Impact", "Remediation"):
            if not re.search(rf"^## {heading}\s*$", body, flags=re.MULTILINE):
                errors.append(f"missing ## {heading} section")
    return errors


def build_finding_quality_tool(backend: Any) -> BaseTool:
    """Bind the structural checker to the active engagement's filesystem."""

    @tool
    def check_finding_report(path: str, runtime: ToolRuntime) -> str:
        """Check a canonical finding's verdict, CVSS math, sections and evidence paths."""
        scoped = EngagementFilesystemBackend(backend, runtime.state.get("workspace_path"))
        result = scoped.read(f"/workspace/{path}", limit=10_000)
        if result.error or result.file_data is None:
            return json.dumps({"valid": False, "errors": [result.error or "finding unreadable"]})

        def exists(relative: str) -> bool:
            evidence = scoped.read(f"/workspace/{relative}", limit=1)
            return evidence.error is None and evidence.file_data is not None

        errors = check_finding_content(path, result.file_data["content"], evidence_exists=exists)
        return json.dumps({"valid": not errors, "errors": errors})

    return check_finding_report
