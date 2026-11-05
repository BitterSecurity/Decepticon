"""Workspace-first finding verification without a knowledge-graph dependency."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from decepticon.tools.bash.bash import get_sandbox
from decepticon.tools.research.poc import parse_cvss_vector, sandbox_runner, validate_poc


def _patterns(raw: str) -> list[str]:
    return [pattern.strip() for pattern in raw.split(",") if pattern.strip()]


@tool
async def validate_workspace_finding(
    finding_id: str,
    poc_command: str,
    success_patterns: str,
    negative_command: str,
    negative_patterns: str,
    cvss_vector: str,
) -> str:
    """Run a minimal PoC and its mandatory baseline control in the sandbox.

    This verifier is intentionally workspace-first: it does not depend on the
    deferred Neo4j integration and does not promote a graph node.  Persist the
    returned JSON in the finding's evidence directory before reporting a
    validated result.

    ``success_patterns`` and ``negative_patterns`` are comma-separated Python
    regular expressions.  A result validates only when the positive command
    matches a success pattern, the baseline matches a baseline pattern, and the
    baseline does not match any success pattern.
    """
    required = {
        "finding_id": finding_id,
        "poc_command": poc_command,
        "success_patterns": success_patterns,
        "negative_command": negative_command,
        "negative_patterns": negative_patterns,
        "cvss_vector": cvss_vector,
    }
    empty = [name for name, value in required.items() if not value or not value.strip()]
    if empty:
        return json.dumps(
            {"error": "mandatory verification fields are missing", "fields": empty},
            sort_keys=True,
        )
    sandbox = get_sandbox()
    if sandbox is None:
        return json.dumps({"error": "HTTPTransportSandbox not initialized"}, sort_keys=True)
    try:
        cvss = parse_cvss_vector(cvss_vector)
    except ValueError as exc:
        return json.dumps({"error": f"bad CVSS vector: {exc}"}, sort_keys=True)
    positive = _patterns(success_patterns)
    negative = _patterns(negative_patterns)
    if not positive or not negative:
        return json.dumps(
            {"error": "success_patterns and negative_patterns must each contain a pattern"},
            sort_keys=True,
        )
    result = await validate_poc(
        vuln_id=finding_id,
        poc_command=poc_command,
        success_patterns=positive,
        runner=sandbox_runner(sandbox),
        negative_command=negative_command,
        negative_patterns=negative,
        cvss=cvss,
    )
    return json.dumps(result.to_dict(), sort_keys=True)


WORKSPACE_VERIFICATION_TOOLS = [validate_workspace_finding]
