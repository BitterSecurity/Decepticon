"""LLM-judge finding deduplication.

Compares new findings against existing reports using both deterministic
(CVE×package identity) and LLM-judged (root-cause equivalence) methods.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


def _findings_path() -> Path:
    return _workspace() / "findings"


def _load_findings() -> list[dict]:
    d = _findings_path()
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
def check_duplicate(
    title: str,
    description: str,
    cve_ids: str = "",
    finding_class: str = "dynamic",
    target_surface: str = "",
) -> str:
    """Check if a finding is likely a duplicate of an existing one.

    WHEN TO USE: Before filing a new finding, check if it duplicates one
    already filed. Uses deterministic matching for dependency CVEs and
    heuristic root-cause matching for dynamic findings.

    Args:
        title: The finding title
        description: The finding description
        cve_ids: Comma-separated CVE IDs
        finding_class: dynamic or dependency_cve
        target_surface: The endpoint/surface being tested
    """
    existing = _load_findings()
    if not existing:
        return _json({"is_duplicate": False, "reason": "no existing findings"})

    cve_list = [c.strip() for c in cve_ids.split(",") if c.strip()] if cve_ids else []

    # Deterministic: CVE×finding_class identity for dependency findings
    if finding_class == "dependency_cve" and cve_list:
        for f in existing:
            if f.get("finding_class") == "dependency_cve":
                existing_cves = set(f.get("cve_ids", []))
                if set(cve_list) & existing_cves:
                    return _json(
                        {
                            "is_duplicate": True,
                            "duplicate_of": f["id"],
                            "match_type": "cve_identity",
                            "matched_cves": list(set(cve_list) & existing_cves),
                            "recommendation": f"Use update_finding('{f['id']}', ...) to add new information",
                        }
                    )

    # Heuristic: title/description similarity for dynamic findings
    title_lower = title.lower()
    desc_words = set(description.lower().split())

    for f in existing:
        # Exact title match
        if f.get("title", "").lower() == title_lower:
            return _json(
                {
                    "is_duplicate": True,
                    "duplicate_of": f["id"],
                    "match_type": "exact_title",
                    "recommendation": f"Use update_finding('{f['id']}', ...) instead of filing a new finding",
                }
            )

        # High word overlap in description
        existing_words = set(f.get("description", "").lower().split())
        if existing_words and desc_words:
            overlap = len(desc_words & existing_words) / max(len(desc_words), 1)
            if overlap > 0.7:
                return _json(
                    {
                        "is_duplicate": True,
                        "duplicate_of": f["id"],
                        "match_type": "description_similarity",
                        "overlap": round(overlap, 2),
                        "recommendation": f"Review finding '{f['id']}' — descriptions are {int(overlap * 100)}% similar",
                    }
                )

        # Same endpoint/surface + same CWE pattern
        if target_surface and target_surface in f.get("description", ""):
            return _json(
                {
                    "is_duplicate": False,
                    "potential_related": f["id"],
                    "match_type": "same_surface",
                    "note": "Same surface but possibly different root cause — review before filing",
                }
            )

    return _json(
        {"is_duplicate": False, "reason": "no matches found", "existing_count": len(existing)}
    )


@tool
def dedupe_all_findings() -> str:
    """Scan all findings for potential duplicates.

    WHEN TO USE: Before finishing an engagement, scan for duplicates
    that may have been filed by parallel agents.
    """
    findings = _load_findings()
    potential_dupes = []

    for i, f1 in enumerate(findings):
        for f2 in findings[i + 1 :]:
            # CVE overlap
            cves1 = set(f1.get("cve_ids", []))
            cves2 = set(f2.get("cve_ids", []))
            if cves1 and cves2 and cves1 & cves2:
                potential_dupes.append(
                    {
                        "finding_a": f1["id"],
                        "finding_b": f2["id"],
                        "match_type": "shared_cves",
                        "shared": list(cves1 & cves2),
                    }
                )
                continue

            # Title similarity
            if f1.get("title", "").lower() == f2.get("title", "").lower():
                potential_dupes.append(
                    {
                        "finding_a": f1["id"],
                        "finding_b": f2["id"],
                        "match_type": "exact_title",
                    }
                )

    return _json(
        {
            "total_findings": len(findings),
            "potential_duplicates": len(potential_dupes),
            "pairs": potential_dupes,
        }
    )


FINDING_DEDUPE_TOOLS = [check_duplicate, dedupe_all_findings]
