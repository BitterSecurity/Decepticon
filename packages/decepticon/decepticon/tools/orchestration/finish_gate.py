"""Finish-scan gate — enforced engagement termination protocol.

The only way to properly end an engagement. Enforces:
- No active child agents (must all be completed/stopped)
- Coverage reconciliation (check for gaps)
- Attack-chaining gate (must consider chaining findings)
- Mandatory report sections (executive summary, methodology, technical analysis, recommendations)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


@tool
def finish_engagement(
    executive_summary: str,
    methodology: str,
    technical_analysis: str,
    recommendations: str,
    attack_chaining_considered: bool = False,
    attack_chaining_notes: str = "",
) -> str:
    """Complete the engagement with a structured final report.

    WHEN TO USE: Only when ALL testing is complete. This is the ONLY
    way to properly end an engagement. Will REFUSE to complete if:
    - Child agents are still active
    - Coverage has gaps
    - Attack chaining was not considered
    - Required report sections are empty

    Args:
        executive_summary: High-level summary for leadership
        methodology: Testing methodology and scope
        technical_analysis: Detailed technical findings analysis
        recommendations: Prioritized remediation recommendations
        attack_chaining_considered: Must be True — confirms you considered chaining findings
        attack_chaining_notes: How you evaluated chaining confirmed findings
    """
    # Validate all sections non-empty
    sections = {
        "executive_summary": executive_summary,
        "methodology": methodology,
        "technical_analysis": technical_analysis,
        "recommendations": recommendations,
    }
    empty = [k for k, v in sections.items() if not v.strip()]
    if empty:
        return _json({"error": "required sections are empty", "empty_sections": empty})

    # Check attack chaining
    if not attack_chaining_considered:
        return _json(
            {
                "error": "attack_chaining_considered must be True",
                "hint": "Before finishing, review all confirmed findings and genuinely consider whether any can be chained for higher impact. Set attack_chaining_considered=True and provide notes.",
            }
        )

    # Check for active agents
    agents_file = _workspace() / "agents" / "graph.json"
    if agents_file.exists():
        try:
            data = json.loads(agents_file.read_text("utf-8"))
            active = [a for a in data.get("agents", {}).values() if a.get("status") == "running"]
            if active:
                return _json(
                    {
                        "error": f"{len(active)} agents still active",
                        "active_agents": [{"id": a["id"], "name": a.get("name")} for a in active],
                        "hint": "Stop or wait for all agents to complete before finishing.",
                    }
                )
        except (OSError, json.JSONDecodeError):
            pass

    # Check coverage gaps
    coverage_path = _workspace() / "coverage.json"
    coverage_gaps = []
    if coverage_path.exists():
        try:
            cov = json.loads(coverage_path.read_text("utf-8"))
            follow_ups = [
                e for e in cov.get("entries", []) if e.get("outcome") == "needs_follow_up"
            ]
            coverage_gaps = [
                {"surface": e["surface"], "risk_area": e["risk_area"]} for e in follow_ups
            ]
        except (OSError, json.JSONDecodeError):
            pass

    # Load findings summary
    findings_dir = _workspace() / "findings"
    findings_count = 0
    severity_counts: dict[str, int] = {}
    if findings_dir.exists():
        for p in findings_dir.glob("vuln-*.json"):
            try:
                f = json.loads(p.read_text("utf-8"))
                if not f.get("deleted"):
                    findings_count += 1
                    sev = f.get("severity", "unknown")
                    severity_counts[sev] = severity_counts.get(sev, 0) + 1
            except Exception:
                pass

    # Write final report
    report = {
        "status": "completed",
        "completed_at": time.time(),
        "completed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "report": sections,
        "attack_chaining": {"considered": True, "notes": attack_chaining_notes},
        "findings_summary": {"total": findings_count, "severity_counts": severity_counts},
        "coverage_gaps": coverage_gaps,
    }

    report_path = _workspace() / "final_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), "utf-8")

    return _json(
        {
            "finished": True,
            "findings": findings_count,
            "severity_counts": severity_counts,
            "coverage_gaps_remaining": len(coverage_gaps),
            "report_path": str(report_path),
        }
    )


FINISH_GATE_TOOLS = [finish_engagement]
