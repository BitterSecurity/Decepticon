"""Enhanced SARIF 2.1.0 generation with code-scanning grade features.

Extends Decepticon's existing SARIF output with:
- CWE → STRIDE leg tagging
- GitHub security-severity from CVSS vector
- fixes array from fix_before/fix_after
- partialFingerprints for cross-rename dismissal carryover
- Coverage encoded as non-failing results (pass/notApplicable/open)
- versionControlProvenance (git SHA/branch/repo)
- Always-emit-empty for CodeQL auto-resolve
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


# CWE to STRIDE mapping
CWE_STRIDE_MAP: dict[str, str] = {
    # Spoofing
    "CWE-287": "spoofing",
    "CWE-290": "spoofing",
    "CWE-294": "spoofing",
    "CWE-384": "spoofing",
    "CWE-613": "spoofing",
    # Tampering
    "CWE-20": "tampering",
    "CWE-74": "tampering",
    "CWE-79": "tampering",
    "CWE-89": "tampering",
    "CWE-94": "tampering",
    "CWE-352": "tampering",
    # Repudiation
    "CWE-117": "repudiation",
    "CWE-223": "repudiation",
    "CWE-778": "repudiation",
    # Information Disclosure
    "CWE-200": "information_disclosure",
    "CWE-209": "information_disclosure",
    "CWE-319": "information_disclosure",
    "CWE-532": "information_disclosure",
    "CWE-611": "information_disclosure",
    "CWE-918": "information_disclosure",
    # Denial of Service
    "CWE-400": "denial_of_service",
    "CWE-770": "denial_of_service",
    "CWE-834": "denial_of_service",
    # Elevation of Privilege
    "CWE-250": "elevation_of_privilege",
    "CWE-269": "elevation_of_privilege",
    "CWE-276": "elevation_of_privilege",
    "CWE-732": "elevation_of_privilege",
    "CWE-863": "elevation_of_privilege",
    "CWE-862": "elevation_of_privilege",
}


def _git_provenance(workspace: Path) -> dict[str, Any] | None:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=5,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=5,
        ).stdout.strip()
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(workspace),
            timeout=5,
        ).stdout.strip()
        if sha:
            return {"commitSha": sha, "branch": branch, "repositoryUri": remote}
    except Exception:
        pass
    return None


def _cvss_to_security_severity(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "note"


def _partial_fingerprint(rule_id: str, uri: str, line: int) -> str:
    data = f"{rule_id}:{uri}:{line}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


@tool
def generate_enhanced_sarif(output_path: str = "") -> str:
    """Generate an enhanced SARIF 2.1.0 report from engagement findings.

    WHEN TO USE: At engagement end for CI/CD integration or code-scanning
    tool import (GitHub Advanced Security, CodeQL, etc.).

    Includes STRIDE tags, security-severity, partial fingerprints,
    fix suggestions, coverage results, and git provenance.
    """
    workspace = Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))
    findings_dir = workspace / "findings"

    if not output_path:
        export_dir = workspace / "export"
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(export_dir / "results.sarif")

    # Load findings
    findings = []
    if findings_dir.exists():
        for p in sorted(findings_dir.glob("vuln-*.json")):
            try:
                f = json.loads(p.read_text("utf-8"))
                if not f.get("deleted"):
                    findings.append(f)
            except Exception:
                pass

    # Build SARIF
    rules = []
    results = []

    for f in findings:
        rule_id = f.get("id", "unknown")
        cvss_score = f.get("cvss", {}).get("score", 0.0)
        cvss_vector = f.get("cvss", {}).get("vector", "")
        cwe_ids = f.get("cwe_ids", [])

        # STRIDE tags from CWE
        stride_tags = list({CWE_STRIDE_MAP.get(c, "") for c in cwe_ids} - {""})

        # Rule
        rule = {
            "id": rule_id,
            "name": f.get("title", ""),
            "shortDescription": {"text": f.get("title", "")},
            "fullDescription": {"text": f.get("description", "")[:2000]},
            "properties": {
                "security-severity": str(cvss_score),
                "tags": ["security"] + stride_tags + [f"cwe/{c}" for c in cwe_ids],
            },
        }
        if cvss_vector:
            rule["properties"]["cvss-vector"] = cvss_vector
        rules.append(rule)

        # Result
        result: dict[str, Any] = {
            "ruleId": rule_id,
            "level": {"critical": "error", "high": "error", "medium": "warning", "low": "note"}.get(
                f.get("severity", "low"), "note"
            ),
            "message": {"text": f.get("description", "")[:1000]},
        }

        # Locations from code_locations
        locations = []
        fixes = []
        for loc in f.get("code_locations", []):
            sarif_loc = {
                "physicalLocation": {
                    "artifactLocation": {"uri": loc.get("file", "")},
                    "region": {
                        "startLine": loc.get("start_line", 1),
                        "endLine": loc.get("end_line", loc.get("start_line", 1)),
                    },
                },
            }
            locations.append(sarif_loc)

            # Fingerprint
            result.setdefault("partialFingerprints", {})
            result["partialFingerprints"]["primaryLocationLineHash"] = _partial_fingerprint(
                rule_id, loc.get("file", ""), loc.get("start_line", 0)
            )

            # Fix from fix_before/fix_after
            if loc.get("fix_after"):
                fix = {
                    "description": {"text": f"Apply fix for {rule_id}"},
                    "artifactChanges": [
                        {
                            "artifactLocation": {"uri": loc.get("file", "")},
                            "replacements": [
                                {
                                    "deletedRegion": {
                                        "startLine": loc.get("start_line", 1),
                                        "endLine": loc.get("end_line", loc.get("start_line", 1)),
                                    },
                                    "insertedContent": {"text": loc.get("fix_after", "")},
                                }
                            ],
                        }
                    ],
                }
                fixes.append(fix)

        if locations:
            result["locations"] = locations
        if fixes:
            result["fixes"] = fixes

        results.append(result)

    # Coverage as non-failing results
    coverage_path = workspace / "coverage.json"
    if coverage_path.exists():
        try:
            cov = json.loads(coverage_path.read_text("utf-8"))
            for entry in cov.get("entries", []):
                outcome = entry.get("outcome", "")
                kind = {
                    "no_issue_found": "pass",
                    "ruled_out": "pass",
                    "not_applicable": "notApplicable",
                    "needs_follow_up": "open",
                }.get(outcome)
                if kind and outcome != "reported":
                    results.append(
                        {
                            "ruleId": f"coverage/{entry.get('risk_area', 'unknown')}",
                            "kind": kind,
                            "level": "none",
                            "message": {
                                "text": f"{entry.get('surface', '')}: {entry.get('risk_area', '')} - {outcome}"
                            },
                        }
                    )
        except Exception:
            pass

    # Git provenance
    provenance = _git_provenance(workspace)
    invocation: dict[str, Any] = {"executionSuccessful": True}
    version_control = []
    if provenance:
        version_control.append(
            {
                "repositoryUri": provenance.get("repositoryUri", ""),
                "revisionId": provenance.get("commitSha", ""),
                "branch": provenance.get("branch", ""),
            }
        )

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Decepticon",
                        "informationUri": "https://github.com/BitterSecurity/Decepticon",
                        "rules": rules,
                    },
                },
                "invocations": [invocation],
                "results": results,
                "versionControlProvenance": version_control,
            }
        ],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(json.dumps(sarif, indent=2), "utf-8")

    return _json(
        {
            "generated": True,
            "path": output_path,
            "findings": len(findings),
            "coverage_results": len(results) - len(findings),
            "has_git_provenance": provenance is not None,
        }
    )


SARIF_ENHANCED_TOOLS = [generate_enhanced_sarif]
