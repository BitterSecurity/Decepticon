"""Diff-scoped scanning — focus testing on changed code.

For CI/CD PR reviews: extracts the diff, identifies changed files and
functions, and generates a scoped testing plan focused on the delta.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _workspace() -> Path:
    return Path(os.environ.get("DECEPTICON_WORKSPACE_PATH", "/workspace"))


@tool
def get_diff_scope(diff_base: str = "main", diff_head: str = "HEAD") -> str:
    """Extract the diff scope between two git refs.

    WHEN TO USE: In CI/CD PR scanning mode. Identifies what changed
    so testing can focus on the security-relevant delta.

    Args:
        diff_base: Base ref (e.g. "main", "origin/main")
        diff_head: Head ref (e.g. "HEAD", branch name)
    """
    ws = _workspace()
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", f"{diff_base}...{diff_head}"],
            capture_output=True,
            text=True,
            cwd=str(ws),
            timeout=30,
        )
        if result.returncode != 0:
            return _json({"error": f"git diff failed: {result.stderr.strip()}"})
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return _json({"error": str(e)})

    changes: list[dict] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, filepath = parts
            ext = Path(filepath).suffix
            changes.append(
                {
                    "status": {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}.get(
                        status[0], status
                    ),
                    "file": filepath,
                    "extension": ext,
                    "security_relevant": _is_security_relevant(filepath, ext),
                }
            )

    security_files = [c for c in changes if c["security_relevant"]]

    return _json(
        {
            "total_changes": len(changes),
            "security_relevant": len(security_files),
            "diff_base": diff_base,
            "diff_head": diff_head,
            "changes": changes,
            "priority_files": [c["file"] for c in security_files],
        }
    )


def _is_security_relevant(filepath: str, ext: str) -> bool:
    """Heuristic: is this file change security-relevant?"""
    fp = filepath.lower()
    # Auth/security files
    if any(
        k in fp
        for k in [
            "auth",
            "login",
            "session",
            "token",
            "password",
            "crypto",
            "permission",
            "access",
            "security",
            "middleware",
            "guard",
            "policy",
            "rbac",
            "acl",
            "oauth",
            "jwt",
            "cors",
            "csrf",
            "sanitiz",
            "validat",
            "escap",
            "encod",
        ]
    ):
        return True
    # Config/secrets
    if any(
        fp.endswith(f)
        for f in [
            ".env",
            ".env.example",
            "docker-compose.yml",
            "dockerfile",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
        ]
    ):
        return True
    # API routes
    if any(
        k in fp for k in ["route", "controller", "handler", "endpoint", "api", "view", "resolver"]
    ):
        return True
    # Code files (always somewhat relevant)
    if ext in {".py", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".php", ".cs", ".sol"}:
        return True
    return False


@tool
def generate_diff_test_plan(diff_json: str) -> str:
    """Generate a security test plan focused on diff changes.

    WHEN TO USE: After get_diff_scope, generate targeted test cases
    for the changed code.

    Args:
        diff_json: Output from get_diff_scope
    """
    try:
        diff = json.loads(diff_json)
    except json.JSONDecodeError:
        return _json({"error": "invalid diff_json"})

    changes = diff.get("changes", [])
    test_plan: list[dict] = []

    for change in changes:
        if not change.get("security_relevant"):
            continue
        fp = change["file"].lower()
        tests = []

        if any(k in fp for k in ["auth", "login", "session", "password"]):
            tests.extend(["authentication_bypass", "session_fixation", "credential_exposure"])
        if any(k in fp for k in ["api", "route", "controller", "handler"]):
            tests.extend(["injection", "idor", "broken_access_control", "rate_limiting"])
        if any(k in fp for k in ["upload", "file"]):
            tests.extend(["file_upload_bypass", "path_traversal"])
        if any(k in fp for k in ["template", "render", "view"]):
            tests.extend(["xss", "ssti"])
        if any(k in fp for k in ["query", "sql", "db", "model"]):
            tests.extend(["sql_injection", "nosql_injection"])
        if any(k in fp for k in ["redirect", "url", "link"]):
            tests.extend(["open_redirect", "ssrf"])
        if change["extension"] == ".sol":
            tests.extend(["reentrancy", "access_control", "integer_overflow"])
        if not tests:
            tests = ["general_code_review"]

        test_plan.append(
            {
                "file": change["file"],
                "change_type": change["status"],
                "suggested_tests": list(set(tests)),
                "priority": "high" if change["status"] == "added" else "medium",
            }
        )

    return _json(
        {
            "total_test_targets": len(test_plan),
            "plan": test_plan,
        }
    )


DIFF_SCOPE_TOOLS = [get_diff_scope, generate_diff_test_plan]
