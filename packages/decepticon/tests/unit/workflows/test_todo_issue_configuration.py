"""Prevent the five reported task-documentation false positives without changing scan syntax."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
WORKFLOW = REPO_ROOT / ".github/workflows/todo-to-issue.yml"
SOURCE = REPO_ROOT / "packages/decepticon/decepticon/tools/research/agent_todo.py"
ACTION = "alstr/todo-to-issue-action@64aca8fda7023259aada83ba44ad988c4c443657"


@pytest.fixture
def scanner_inputs() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(
        step["with"] for step in workflow["jobs"]["scan"]["steps"] if step.get("uses") == ACTION
    )


@pytest.mark.parametrize(
    ("owner", "prefix"),
    [
        ("module", "Each agent maintains"),
        ("create_todo", "agent_name:"),
        ("list_todos", "List "),
        ("mark_todo_done", "todo_id:"),
        ("delete_todo", "todo_id:"),
    ],
)
def test_reported_docstring_lines_contain_no_task_marker(
    scanner_inputs: dict, owner: str, prefix: str
) -> None:
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = (
        module
        if owner == "module"
        else next(
            child
            for child in module.body
            if isinstance(child, ast.FunctionDef) and child.name == owner
        )
    )
    docstring = ast.get_docstring(node)
    assert docstring is not None
    line = next(line.strip() for line in docstring.splitlines() if line.strip().startswith(prefix))
    for identifier in json.loads(scanner_inputs["IDENTIFIERS"]):
        assert not re.search(r"\b" + re.escape(identifier["name"]) + r"\b", line, re.IGNORECASE)


def test_scanner_retains_default_python_filename_and_comment_mapping(scanner_inputs: dict) -> None:
    assert "LANGUAGES" not in scanner_inputs
    assert scanner_inputs.get("NO_STANDARD", False) is False
    assert not (REPO_ROOT / ".github/todo-python-language.json").exists()


@pytest.mark.parametrize(
    "path", [".github/workflows/todo-to-issue.yml", ".github/todo-python-language.json"]
)
def test_scanner_configuration_changes_trigger_python_ci(path: str) -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    app_filter = next(
        step for step in workflow["jobs"]["changes"]["steps"] if step.get("id") == "app"
    )
    assert path in yaml.safe_load(app_filter["with"]["filters"])["python"]


def test_scanner_preserves_marker_labels(scanner_inputs: dict) -> None:
    assert json.loads(scanner_inputs["IDENTIFIERS"]) == [
        {"name": "TODO", "labels": ["todo"]},
        {"name": "FIXME", "labels": ["todo", "bug"]},
        {"name": "HACK", "labels": ["todo", "tech-debt"]},
    ]


def test_scanner_preserves_read_only_checkout() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    checkout = workflow["jobs"]["scan"]["steps"][0]
    assert workflow["permissions"] == {"contents": "read", "issues": "write"}
    assert checkout["with"]["persist-credentials"] is False


def test_scanner_does_not_insert_issue_urls(scanner_inputs: dict) -> None:
    assert scanner_inputs["INSERT_ISSUE_URLS"] is False
