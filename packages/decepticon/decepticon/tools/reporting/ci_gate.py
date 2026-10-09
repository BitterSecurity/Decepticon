"""CI pipeline gate — exit code based on finding severity.

Provides tools for CI/CD integration: evaluate findings against a severity
threshold and produce appropriate exit codes for pipeline blocking.

Exit codes:
  0 = clean (no findings above threshold)
  1 = fatal error
  2 = vulnerabilities found above threshold
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


SEVERITY_ORDER = ["none", "low", "medium", "high", "critical"]


def _severity_rank(s: str) -> int:
    try:
        return SEVERITY_ORDER.index(s.lower())
    except ValueError:
        return len(SEVERITY_ORDER)  # unknown = always triggers


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _load_findings() -> list[dict]:
    d = _workspace() / "findings"
    if not d.exists():
        return []
    findings = []
    for p in sorted(d.glob("vuln-*.json")):
        try:
            f = json.loads(p.read_text("utf-8"))
            if not f.get("deleted"):
                findings.append(f)
        except Exception:
            pass
    return findings


@tool
def ci_evaluate(fail_on: str = "high") -> str:
    """Evaluate findings against a severity threshold for CI gating.

    WHEN TO USE: At the end of a CI/CD scan to determine if the pipeline
    should pass or fail based on finding severity.

    Args:
        fail_on: Minimum severity to trigger failure.
                 One of: low, medium, high, critical.
                 Unknown severities always trigger.

    Returns:
        JSON with exit_code (0=clean, 2=fail), findings above threshold,
        and severity breakdown.
    """
    threshold_rank = _severity_rank(fail_on)
    findings = _load_findings()

    severity_counts: dict[str, int] = {}
    above_threshold: list[dict] = []

    for f in findings:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        if _severity_rank(sev) >= threshold_rank:
            above_threshold.append(
                {
                    "id": f.get("id"),
                    "title": f.get("title"),
                    "severity": sev,
                    "cvss_score": f.get("cvss", {}).get("score"),
                }
            )

    exit_code = 2 if above_threshold else 0

    return _json(
        {
            "exit_code": exit_code,
            "pass": exit_code == 0,
            "fail_on": fail_on,
            "threshold_rank": threshold_rank,
            "total_findings": len(findings),
            "above_threshold": len(above_threshold),
            "severity_counts": severity_counts,
            "blocking_findings": above_threshold,
            "summary": f"{'PASS' if exit_code == 0 else 'FAIL'}: {len(above_threshold)} finding(s) at or above '{fail_on}' severity",
        }
    )


@tool
def ci_summary() -> str:
    """Generate a compact CI-friendly summary of scan results."""
    findings = _load_findings()
    severity_counts: dict[str, int] = {}
    for f in findings:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    # Read coverage
    coverage_path = _workspace() / "coverage.json"
    coverage_total = 0
    if coverage_path.exists():
        try:
            cov = json.loads(coverage_path.read_text("utf-8"))
            coverage_total = len(cov.get("entries", []))
        except Exception:
            pass

    return _json(
        {
            "total_findings": len(findings),
            "severity_counts": severity_counts,
            "coverage_entries": coverage_total,
            "critical": severity_counts.get("critical", 0),
            "high": severity_counts.get("high", 0),
            "medium": severity_counts.get("medium", 0),
            "low": severity_counts.get("low", 0),
        }
    )


CI_GATE_TOOLS = [ci_evaluate, ci_summary]
