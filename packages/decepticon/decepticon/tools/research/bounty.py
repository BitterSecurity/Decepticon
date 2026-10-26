"""Bug bounty tools — scope checking and report generation.

Provides two tools for bug bounty workflows:

- ``bounty_scope_check``  — validate target + vuln class against program scope
- ``format_bounty_report`` — generate a platform-ready report from a FINDING node

These complement the existing vulnresearch pipeline by adding bounty-specific
quality gates after ``validate_finding`` confirms exploitability.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from langchain_core.tools import tool

from decepticon.tools.research._state import _json, _load, _save
from decepticon_core.types.kg import (
    EdgeKind,
    KnowledgeGraph,
    Node,
    NodeKind,
)
from decepticon_core.types.roe import matches_domain_glob
from decepticon_core.utils.logging import get_logger

log = get_logger("research.bounty")

# Common vuln classes that many programs exclude.
_COMMONLY_EXCLUDED = {
    "self-xss",
    "rate-limiting",
    "missing-rate-limit",
    "clickjacking",
    "content-spoofing",
    "text-injection",
    "csv-injection",
    "missing-security-headers",
    "missing-best-practices",
    "social-engineering",
    "physical-access",
    "denial-of-service",
    "dos",
    "logout-csrf",
    "login-csrf",
    "open-redirect-low",
    "email-enumeration",
    "username-enumeration",
    "version-disclosure",
    "stack-trace",
    "verbose-error",
    "autocomplete",
    "tabnabbing",
}


def _normalize_class(cls: str) -> str:
    return cls.strip().lower().replace(" ", "-").replace("_", "-")


@tool
def bounty_scope_check(
    target: str,
    vuln_class: str,
    program_url: str = "",
    excluded_classes: str = "[]",
    in_scope_domains: str = "[]",
) -> str:
    """Check whether a finding is in scope for a bug bounty program.

    WHEN TO USE: Before writing or submitting any bug bounty report. Call this
    after ``validate_finding`` succeeds to verify the target and vulnerability
    class are not excluded by the program's policy.

    A failed scope check is a rejected report waiting to happen. Run this
    BEFORE spending time on report formatting.

    Args:
        target: The target hostname, URL, or package name under test.
        vuln_class: The vulnerability class (e.g. "sqli", "ssrf", "xss",
            "rce", "idor", "path-traversal", "dos").
        program_url: URL of the bounty program scope page (informational).
        excluded_classes: JSON array of vuln classes the program excludes.
            Example: ``'["dos", "self-xss", "clickjacking"]'``
        in_scope_domains: JSON array of in-scope domain patterns.
            Example: ``'["*.example.com", "api.example.com"]'``

    Returns:
        JSON with ``in_scope`` (bool), ``warnings`` (list of strings),
        and the recorded scope-check node id.
    """
    warnings: list[str] = []
    in_scope = True

    # Parse exclusions
    try:
        exclusions = {_normalize_class(c) for c in json.loads(excluded_classes)}
    except (json.JSONDecodeError, TypeError):
        exclusions = set()

    # Parse in-scope domains
    try:
        domains = json.loads(in_scope_domains)
    except (json.JSONDecodeError, TypeError):
        domains = []

    normalized_class = _normalize_class(vuln_class)

    # Check against program exclusions
    if normalized_class in exclusions:
        in_scope = False
        warnings.append(f"Vuln class '{vuln_class}' is explicitly excluded by the program")

    # Check against commonly excluded classes
    if normalized_class in _COMMONLY_EXCLUDED and normalized_class not in exclusions:
        warnings.append(
            f"Vuln class '{vuln_class}' is commonly excluded by bounty programs — "
            "verify with the specific program's scope before submitting"
        )

    # Check domain scope if provided
    if domains:
        target_lower = target.lower()
        domain_match = False
        for pattern in domains:
            pattern = pattern.lower().strip()
            if pattern.startswith("*."):
                if matches_domain_glob(pattern, target_lower):
                    domain_match = True
                    break
            elif target_lower == pattern or target_lower.endswith("/" + pattern):
                domain_match = True
                break
        if not domain_match:
            in_scope = False
            warnings.append(f"Target '{target}' does not match any in-scope domain: {domains}")

    # Severity-based warnings
    low_impact_classes = {"information-disclosure", "version-disclosure", "verbose-error"}
    if normalized_class in low_impact_classes:
        warnings.append(
            "Low-impact finding — many programs ignore informational/low severity. "
            "Consider whether this is worth submitting."
        )

    # Record in the knowledge graph
    graph, path = _load()
    scope_node = graph.upsert_node(
        Node.make(
            NodeKind.HYPOTHESIS,
            f"scope-check: {target} / {vuln_class}",
            key=f"scope-check::{target}::{normalized_class}",
            target=target,
            vuln_class=vuln_class,
            in_scope=in_scope,
            program_url=program_url,
            checked_at=time.time(),
            warnings=warnings,
        )
    )
    _save(graph, path)

    return _json(
        {
            "in_scope": in_scope,
            "target": target,
            "vuln_class": vuln_class,
            "warnings": warnings,
            "node_id": scope_node.id,
        }
    )


def _find_linked_vuln(graph: KnowledgeGraph, finding_id: str) -> Node | None:
    """Walk VALIDATES edges from a FINDING to find the linked VULNERABILITY."""
    for edge in graph.edges.values():
        if edge.src == finding_id and edge.kind == EdgeKind.VALIDATES:
            vuln = graph.nodes.get(edge.dst)
            if vuln and vuln.kind == NodeKind.VULNERABILITY:
                return vuln
    # Fallback: check MAPS_TO edges
    for edge in graph.edges.values():
        if edge.src == finding_id and edge.kind == EdgeKind.MAPS_TO:
            vuln = graph.nodes.get(edge.dst)
            if vuln and vuln.kind == NodeKind.VULNERABILITY:
                return vuln
    return None


def _severity_label(score: float) -> str:
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    if score > 0.0:
        return "Low"
    return "Informational"


@tool
def format_bounty_report(
    finding_id: str,
    platform: str = "hackerone",
    program_name: str = "",
    component_name: str = "",
) -> str:
    """Generate a bug bounty report from a validated FINDING node.

    WHEN TO USE: After ``validate_finding`` confirms a vulnerability is real
    and ``bounty_scope_check`` confirms it's in scope. Generates a
    platform-ready report at ``workspace/findings/BOUNTY-{id}.md``.

    The report follows the title convention: ``Component: VulnClass via Mechanism``
    and includes full CVSS vector, PoC, and remediation — optimized for
    acceptance rate, not word count.

    Args:
        finding_id: The FINDING node id from the knowledge graph (returned
            by ``validate_finding``).
        platform: Target platform — ``hackerone``, ``bugcrowd``, ``immunefi``,
            or ``github``.
        program_name: Name of the bounty program (informational).
        component_name: Name of the affected component/package for the title.

    Returns:
        JSON with the report file path and a preview of the title + severity.
    """
    graph, kg_path = _load()

    finding = graph.nodes.get(finding_id)
    if finding is None:
        return _json({"error": f"FINDING node {finding_id} not found in graph"})

    if finding.kind != NodeKind.FINDING:
        return _json({"error": f"Node {finding_id} is {finding.kind.value}, not finding"})

    # Pull linked vulnerability
    vuln = _find_linked_vuln(graph, finding_id)

    # Extract data
    props = finding.props
    vuln_props = vuln.props if vuln else {}
    validated = props.get("validated", False)
    if not validated:
        return _json({"error": "Finding is not validated — run validate_finding first"})

    cvss_vector = vuln_props.get("cvss_vector", props.get("cvss_vector", ""))
    cvss_score = vuln_props.get("cvss_score", props.get("cvss_score", 0.0))
    severity = _severity_label(float(cvss_score)) if cvss_score else "Unknown"
    cwe_list = vuln_props.get("cwe", [])
    file_path = vuln_props.get("file", "")
    line = vuln_props.get("line", "")
    stdout = props.get("stdout_excerpt", "")
    vuln_label = vuln.label if vuln else finding.label

    # Build title
    comp = component_name or program_name or "Target"
    # Strip "validated: " prefix from finding labels
    clean_label = re.sub(r"^(validated|rejected):\s*", "", vuln_label)
    title = f"{comp}: {clean_label}"

    # Build CWE string
    cwe_str = ", ".join(cwe_list) if isinstance(cwe_list, list) else str(cwe_list)

    # Build report
    report_lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"{clean_label}.",
        f"Affected file: `{file_path}`" + (f" (line {line})" if line else ""),
        "",
        "## Severity",
        "",
        f"**CVSS 3.1**: `{cvss_vector}` ({cvss_score} {severity})",
        "",
    ]

    if cwe_str:
        report_lines.extend([f"**CWE**: {cwe_str}", ""])

    report_lines.extend(
        [
            "## Steps to Reproduce",
            "",
            "_(Fill in exact reproduction steps from the PoC command)_",
            "",
            "## Proof of Concept",
            "",
            "```",
            stdout[:1200] if stdout else "_(attach PoC output)_",
            "```",
            "",
            "## Impact",
            "",
            f"Validated with CVSS {cvss_score} ({severity}).",
            "_(Describe only the demonstrated impact — do not extrapolate)_",
            "",
            "## Remediation",
            "",
            "_(Provide a specific code fix — not generic advice)_",
            "",
            "---",
            f"_Generated by Decepticon vulnresearch pipeline for {platform}_",
            f"_Program: {program_name}_" if program_name else "",
            f"_Finding ID: {finding_id}_",
        ]
    )

    report_content = "\n".join(report_lines)

    # Write report file
    short_id = finding_id[:8]
    report_path = Path("/workspace/findings") / f"BOUNTY-{short_id}.md"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_content, encoding="utf-8")
        wrote = True
    except OSError as e:
        log.warning("Could not write bounty report: %s", e)
        wrote = False

    # Ground-truth telemetry: forward the finding's structured classification
    # (severity / cwe / mitre) — never the target, file path, or PoC. No-op
    # unless the user opted into telemetry.
    try:
        from decepticon.telemetry.sink import get_sink

        get_sink().record_finding(
            severity=str(severity).lower() if severity and severity != "Unknown" else None,
            cwe=[c for c in cwe_list if isinstance(c, str)] if isinstance(cwe_list, list) else None,
            mitre=[m for m in vuln_props.get("mitre", []) if isinstance(m, str)] or None,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the tool
        pass

    return _json(
        {
            "title": title,
            "severity": severity,
            "cvss_score": cvss_score,
            "cvss_vector": cvss_vector,
            "platform": platform,
            "report_path": str(report_path) if wrote else None,
            "preview": report_content[:500],
        }
    )


BOUNTY_TOOLS = [bounty_scope_check, format_bounty_report]
