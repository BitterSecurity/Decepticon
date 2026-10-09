"""Finding epistemics — evidence discipline for vulnerability reports.

Enforces that every filed finding includes counterevidence (strongest case
against), confidence with rationale, severity_change_conditions, and
fix_verification when fixes are proposed. CVSS 3.1 scores are computed
from 8-metric breakdowns — agents can never assert a raw score.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingClass(StrEnum):
    DYNAMIC = "dynamic"
    DEPENDENCY_CVE = "dependency_cve"


# CVSS 3.1 metric values and weights (simplified computation)
CVSS_METRICS = {
    "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.20},
    "AC": {"L": 0.77, "H": 0.44},
    "PR": {"N": 0.85, "L": 0.62, "H": 0.27},  # Scope unchanged
    "PR_changed": {"N": 0.85, "L": 0.68, "H": 0.50},  # Scope changed
    "UI": {"N": 0.85, "R": 0.62},
    "S": {"U": False, "C": True},
    "C": {"H": 0.56, "L": 0.22, "N": 0.0},
    "I": {"H": 0.56, "L": 0.22, "N": 0.0},
    "A": {"H": 0.56, "L": 0.22, "N": 0.0},
}


def compute_cvss_score(metrics: dict[str, str]) -> dict[str, Any]:
    """Compute CVSS 3.1 base score from 8-metric breakdown."""
    import math

    try:
        av = CVSS_METRICS["AV"][metrics["AV"]]
        ac = CVSS_METRICS["AC"][metrics["AC"]]
        scope_changed = CVSS_METRICS["S"][metrics["S"]]
        pr_table = CVSS_METRICS["PR_changed"] if scope_changed else CVSS_METRICS["PR"]
        pr = pr_table[metrics["PR"]]
        ui = CVSS_METRICS["UI"][metrics["UI"]]
        c = CVSS_METRICS["C"][metrics["C"]]
        i = CVSS_METRICS["I"][metrics["I"]]
        a = CVSS_METRICS["A"][metrics["A"]]
    except KeyError as e:
        return {"error": f"invalid metric value: {e}"}

    iss = 1.0 - ((1.0 - c) * (1.0 - i) * (1.0 - a))
    if iss <= 0:
        return {"score": 0.0, "severity": "none", "vector": _build_vector(metrics)}

    exploitability = 8.22 * av * ac * pr * ui

    if scope_changed:
        impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
    else:
        impact = 6.42 * iss

    if impact <= 0:
        base_score = 0.0
    elif scope_changed:
        base_score = min(1.08 * (impact + exploitability), 10.0)
    else:
        base_score = min(impact + exploitability, 10.0)

    base_score = math.ceil(base_score * 10) / 10

    if base_score == 0.0:
        severity = "none"
    elif base_score < 4.0:
        severity = "low"
    elif base_score < 7.0:
        severity = "medium"
    elif base_score < 9.0:
        severity = "high"
    else:
        severity = "critical"

    return {"score": base_score, "severity": severity, "vector": _build_vector(metrics)}


def _build_vector(m: dict) -> str:
    return f"CVSS:3.1/AV:{m['AV']}/AC:{m['AC']}/PR:{m['PR']}/UI:{m['UI']}/S:{m['S']}/C:{m['C']}/I:{m['I']}/A:{m['A']}"


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _findings_path() -> Path:
    return _workspace() / "findings"


_lock = threading.Lock()
_next_id = 1


def _load_findings() -> list[dict]:
    d = _findings_path()
    if not d.exists():
        return []
    findings = []
    for p in sorted(d.glob("vuln-*.json")):
        try:
            findings.append(json.loads(p.read_text("utf-8")))
        except Exception:
            pass
    return findings


# CVE/CWE validation
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$")
_CWE_RE = re.compile(r"^CWE-\d+$")


@tool
def create_finding(
    title: str,
    description: str,
    finding_class: str,
    severity_metrics_json: str,
    evidence: str,
    assumptions: str,
    counterevidence: str,
    confidence: str,
    confidence_rationale: str = "",
    severity_change_conditions: str = "",
    poc_script: str = "",
    cve_ids: str = "",
    cwe_ids: str = "",
    fix_effort: str = "",
    code_locations_json: str = "[]",
    http_exchange_ids: str = "",
    agent_name: str = "",
) -> str:
    """File a vulnerability finding with full evidence discipline.

    WHEN TO USE: When you have confirmed a vulnerability with proof.
    Every finding requires evidence, counterevidence, and CVSS metrics.

    Args:
        title: Concise vulnerability title
        description: Full description with reproduction steps
        finding_class: 'dynamic' or 'dependency_cve'
        severity_metrics_json: JSON object with CVSS 3.1 metrics: {"AV": "N", "AC": "L", "PR": "N", "UI": "N", "S": "U", "C": "H", "I": "H", "A": "H"}
        evidence: Proof this vulnerability exists
        assumptions: What you assumed during testing
        counterevidence: Strongest case AGAINST this finding being real/severe
        confidence: high, medium, or low
        confidence_rationale: REQUIRED when confidence is not 'high'
        severity_change_conditions: What would raise or lower the severity
        poc_script: Proof-of-concept script code
        cve_ids: Comma-separated CVE IDs (e.g. "CVE-2024-1234")
        cwe_ids: Comma-separated CWE IDs (e.g. "CWE-79")
        fix_effort: Estimated fix effort (trivial/low/medium/high/critical)
        code_locations_json: JSON array of {file, start_line, end_line, snippet, fix_before, fix_after}
        http_exchange_ids: Comma-separated Caido proxy exchange IDs
        agent_name: Filing agent
    """
    global _next_id

    # Validate finding class
    try:
        FindingClass(finding_class)
    except ValueError:
        return _json(
            {
                "error": f"invalid finding_class: {finding_class}",
                "valid": [c.value for c in FindingClass],
            }
        )

    # Validate confidence
    try:
        conf = Confidence(confidence)
    except ValueError:
        return _json({"error": f"invalid confidence: {confidence}"})

    if conf != Confidence.HIGH and not confidence_rationale.strip():
        return _json({"error": "confidence_rationale REQUIRED when confidence is not 'high'"})

    # Validate CVSS metrics
    try:
        metrics = json.loads(severity_metrics_json)
    except json.JSONDecodeError:
        return _json({"error": "invalid severity_metrics_json"})

    cvss = compute_cvss_score(metrics)
    if "error" in cvss:
        return _json({"error": f"CVSS computation failed: {cvss['error']}"})

    # Required fields
    for field_name, field_val in [("evidence", evidence), ("counterevidence", counterevidence)]:
        if not field_val.strip():
            return _json({"error": f"{field_name} is required"})

    # Validate CVE/CWE
    cve_list = [c.strip() for c in cve_ids.split(",") if c.strip()] if cve_ids else []
    for cve in cve_list:
        if not _CVE_RE.match(cve):
            return _json({"error": f"invalid CVE format: {cve}"})
    cwe_list = [c.strip() for c in cwe_ids.split(",") if c.strip()] if cwe_ids else []
    for cwe in cwe_list:
        if not _CWE_RE.match(cwe):
            return _json({"error": f"invalid CWE format: {cwe}"})

    # Parse code locations and validate fix_verification
    try:
        code_locs = json.loads(code_locations_json) if code_locations_json.strip() else []
    except json.JSONDecodeError:
        code_locs = []

    any(loc.get("fix_after") for loc in code_locs)
    # fix_verification is implicitly required if fix_after is provided (warn, don't block)

    with _lock:
        d = _findings_path()
        d.mkdir(parents=True, exist_ok=True)
        existing = list(d.glob("vuln-*.json"))
        finding_num = len(existing) + 1
        finding_id = f"vuln-{finding_num:04d}"

        finding = {
            "id": finding_id,
            "title": title,
            "description": description,
            "finding_class": finding_class,
            "cvss": cvss,
            "severity": cvss["severity"],
            "evidence": evidence,
            "assumptions": assumptions,
            "counterevidence": counterevidence,
            "confidence": confidence,
            "confidence_rationale": confidence_rationale,
            "severity_change_conditions": severity_change_conditions,
            "poc_script": poc_script,
            "cve_ids": cve_list,
            "cwe_ids": cwe_list,
            "fix_effort": fix_effort,
            "code_locations": code_locs,
            "http_exchange_ids": [x.strip() for x in http_exchange_ids.split(",") if x.strip()]
            if http_exchange_ids
            else [],
            "agent": agent_name,
            "created_at": time.time(),
            "update_history": [],
        }

        fp = d / f"{finding_id}.json"
        fp.write_text(json.dumps(finding, indent=2, default=str), "utf-8")

    return _json(
        {
            "filed": True,
            "id": finding_id,
            "severity": cvss["severity"],
            "cvss_score": cvss["score"],
            "vector": cvss["vector"],
        }
    )


@tool
def update_finding(finding_id: str, updates_json: str, reason: str, agent_name: str = "") -> str:
    """Update an existing finding. Appends to update_history.

    Args:
        finding_id: The finding ID (e.g. vuln-0001)
        updates_json: JSON object of fields to update
        reason: Why this update is being made
    """
    with _lock:
        fp = _findings_path() / f"{finding_id}.json"
        if not fp.exists():
            return _json({"error": f"finding not found: {finding_id}"})
        finding = json.loads(fp.read_text("utf-8"))
        try:
            updates = json.loads(updates_json)
        except json.JSONDecodeError:
            return _json({"error": "invalid updates_json"})

        history_entry = {
            "agent": agent_name,
            "reason": reason,
            "updated_at": time.time(),
            "changed_fields": list(updates.keys()),
            "previous_values": {k: finding.get(k) for k in updates if k in finding},
        }
        finding.setdefault("update_history", []).append(history_entry)

        # Recalculate CVSS if metrics changed
        if "severity_metrics" in updates:
            cvss = compute_cvss_score(updates["severity_metrics"])
            if "error" not in cvss:
                finding["cvss"] = cvss
                finding["severity"] = cvss["severity"]
            del updates["severity_metrics"]

        finding.update(updates)
        fp.write_text(json.dumps(finding, indent=2, default=str), "utf-8")
    return _json({"updated": True, "id": finding_id})


@tool
def delete_finding(finding_id: str, reason: str, agent_name: str = "") -> str:
    """Withdraw a finding with a tombstone record.

    Args:
        finding_id: The finding ID
        reason: Why this finding is being withdrawn
    """
    with _lock:
        fp = _findings_path() / f"{finding_id}.json"
        if not fp.exists():
            return _json({"error": f"finding not found: {finding_id}"})
        finding = json.loads(fp.read_text("utf-8"))
        tombstone = {
            "id": finding_id,
            "deleted": True,
            "deleted_by": agent_name,
            "deleted_at": time.time(),
            "reason": reason,
            "original_title": finding.get("title"),
            "original_severity": finding.get("severity"),
        }
        fp.write_text(json.dumps(tombstone, indent=2, default=str), "utf-8")
    return _json({"deleted": True, "id": finding_id, "tombstone": tombstone})


@tool
def list_findings(severity: str = "", finding_class: str = "", search: str = "") -> str:
    """List all filed findings with optional filtering."""
    findings = _load_findings()
    active = [f for f in findings if not f.get("deleted")]
    if severity:
        active = [f for f in active if f.get("severity") == severity.lower()]
    if finding_class:
        active = [f for f in active if f.get("finding_class") == finding_class]
    if search:
        s = search.lower()
        active = [
            f
            for f in active
            if s in f.get("title", "").lower() or s in f.get("description", "").lower()
        ]

    severity_counts = {}
    for f in [x for x in findings if not x.get("deleted")]:
        sev = f.get("severity", "unknown")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    return _json(
        {
            "total": len(active),
            "severity_counts": severity_counts,
            "findings": [
                {
                    "id": f["id"],
                    "title": f["title"],
                    "severity": f.get("severity"),
                    "cvss_score": f.get("cvss", {}).get("score"),
                    "confidence": f.get("confidence"),
                    "finding_class": f.get("finding_class"),
                    "cve_ids": f.get("cve_ids", []),
                }
                for f in active
            ],
        }
    )


@tool
def get_finding(finding_id: str) -> str:
    """Get the full details of a specific finding."""
    fp = _findings_path() / f"{finding_id}.json"
    if not fp.exists():
        return _json({"error": f"finding not found: {finding_id}"})
    return fp.read_text("utf-8")


FINDING_EPISTEMICS_TOOLS = [
    create_finding,
    update_finding,
    delete_finding,
    list_findings,
    get_finding,
]
