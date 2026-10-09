"""Coverage ledger — tracks what was tested and the outcome.

Records per-surface × risk_area entries with outcomes (reported, no_issue_found,
ruled_out, not_applicable, needs_follow_up). Evidence required for non-clean
outcomes. Persisted to workspace/coverage.json atomically.

This enables compliance-grade reporting: proving not just what was found,
but what was checked and found clean.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


class CoverageOutcome(StrEnum):
    REPORTED = "reported"
    NO_ISSUE_FOUND = "no_issue_found"
    RULED_OUT = "ruled_out"
    NOT_APPLICABLE = "not_applicable"
    NEEDS_FOLLOW_UP = "needs_follow_up"


# Outcomes requiring evidence explanation
_EVIDENCE_REQUIRED = {
    CoverageOutcome.RULED_OUT,
    CoverageOutcome.NOT_APPLICABLE,
    CoverageOutcome.NEEDS_FOLLOW_UP,
}


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _coverage_path() -> Path:
    return _workspace() / "coverage.json"


_lock = threading.Lock()


def _load_coverage() -> dict:
    p = _coverage_path()
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"entries": [], "metadata": {"created_at": time.time()}}


def _save_coverage(data: dict) -> None:
    p = _coverage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), "utf-8")
    tmp.replace(p)


@tool
def record_coverage(
    surface: str, risk_area: str, outcome: str, evidence: str = "", agent_name: str = ""
) -> str:
    """Record a coverage entry for a tested surface and risk area.

    WHEN TO USE: After testing a specific attack surface for a specific
    vulnerability class, record the outcome — whether you found something,
    ruled it out, or need follow-up. This builds the coverage map showing
    what was actually tested.

    Args:
        surface: The attack surface tested (e.g. "POST /api/users", "login form", "S3 bucket")
        risk_area: The vulnerability class tested (e.g. "sql_injection", "idor", "xss", "ssrf")
        outcome: One of: reported, no_issue_found, ruled_out, not_applicable, needs_follow_up
        evidence: Required for ruled_out/not_applicable/needs_follow_up — explain why.
        agent_name: The agent recording this (auto-detected if empty)
    """
    try:
        outcome_enum = CoverageOutcome(outcome)
    except ValueError:
        return _json(
            {"error": f"invalid outcome: {outcome!r}", "valid": [o.value for o in CoverageOutcome]}
        )

    if outcome_enum in _EVIDENCE_REQUIRED and not evidence.strip():
        return _json(
            {
                "error": f"evidence required for outcome '{outcome}'",
                "hint": "Explain why this was ruled out or needs follow-up",
            }
        )

    with _lock:
        data = _load_coverage()
        # Check for duplicates
        for entry in data["entries"]:
            if entry["surface"] == surface and entry["risk_area"] == risk_area:
                return _json(
                    {
                        "error": "duplicate",
                        "hint": f"Use update_coverage with entry_id={entry['id']}",
                        "existing": entry,
                    }
                )

        entry = {
            "id": str(uuid.uuid4())[:8],
            "surface": surface,
            "risk_area": risk_area,
            "outcome": outcome,
            "evidence": evidence,
            "agent": agent_name,
            "recorded_at": time.time(),
            "history": [],
        }
        data["entries"].append(entry)
        data["metadata"]["last_updated"] = time.time()
        _save_coverage(data)

    return _json({"recorded": True, "entry": entry})


@tool
def update_coverage(entry_id: str, outcome: str, evidence: str = "") -> str:
    """Update an existing coverage entry with a new outcome.

    WHEN TO USE: When revisiting a previously tested surface/risk_area
    with new information (e.g. ruled_out → reported after finding a vuln).

    Args:
        entry_id: The coverage entry ID from record_coverage or list_coverage
        outcome: New outcome
        evidence: Required for non-clean outcomes
    """
    try:
        outcome_enum = CoverageOutcome(outcome)
    except ValueError:
        return _json({"error": f"invalid outcome: {outcome!r}"})

    if outcome_enum in _EVIDENCE_REQUIRED and not evidence.strip():
        return _json({"error": f"evidence required for outcome '{outcome}'"})

    with _lock:
        data = _load_coverage()
        for entry in data["entries"]:
            if entry["id"] == entry_id:
                entry["history"].append(
                    {
                        "previous_outcome": entry["outcome"],
                        "previous_evidence": entry.get("evidence", ""),
                        "superseded_at": time.time(),
                    }
                )
                entry["outcome"] = outcome
                entry["evidence"] = evidence
                entry["updated_at"] = time.time()
                _save_coverage(data)
                return _json({"updated": True, "entry": entry})
        return _json({"error": f"entry not found: {entry_id}"})


@tool
def list_coverage(outcome: str = "", surface: str = "", risk_area: str = "") -> str:
    """List coverage entries with optional filtering.

    WHEN TO USE: To review what has been tested, find gaps, or check if
    a surface/risk_area has already been covered.

    Args:
        outcome: Filter by outcome (e.g. "needs_follow_up")
        surface: Filter by surface substring
        risk_area: Filter by risk area substring
    """
    data = _load_coverage()
    entries = data.get("entries", [])

    if outcome:
        entries = [e for e in entries if e["outcome"] == outcome]
    if surface:
        entries = [e for e in entries if surface.lower() in e["surface"].lower()]
    if risk_area:
        entries = [e for e in entries if risk_area.lower() in e["risk_area"].lower()]

    outcome_counts = {}
    for e in data.get("entries", []):
        o = e["outcome"]
        outcome_counts[o] = outcome_counts.get(o, 0) + 1

    return _json(
        {
            "total": len(entries),
            "outcome_counts": outcome_counts,
            "entries": [
                {
                    "id": e["id"],
                    "surface": e["surface"],
                    "risk_area": e["risk_area"],
                    "outcome": e["outcome"],
                    "agent": e.get("agent", ""),
                }
                for e in entries
            ],
        }
    )


@tool
def coverage_gaps(skills_json: str = "[]") -> str:
    """Detect coverage gaps — risk areas that were assigned but never tested.

    WHEN TO USE: Before finishing an engagement, check what was missed.

    Args:
        skills_json: JSON array of skill/risk_area names that were assigned
    """
    data = _load_coverage()
    covered_areas = {e["risk_area"] for e in data.get("entries", [])}

    try:
        assigned = json.loads(skills_json) if skills_json.strip() else []
    except json.JSONDecodeError:
        assigned = []

    gaps = [s for s in assigned if s not in covered_areas]
    follow_ups = [e for e in data.get("entries", []) if e["outcome"] == "needs_follow_up"]

    return _json(
        {
            "total_covered": len(covered_areas),
            "assigned_skills": len(assigned),
            "gaps": gaps,
            "gap_count": len(gaps),
            "needs_follow_up": [
                {"id": e["id"], "surface": e["surface"], "risk_area": e["risk_area"]}
                for e in follow_ups
            ],
        }
    )


COVERAGE_TOOLS = [record_coverage, update_coverage, list_coverage, coverage_gaps]
