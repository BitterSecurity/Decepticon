"""Check the scanner's local language contract without running third-party code."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
WORKFLOW = REPO_ROOT / ".github/workflows/todo-to-issue.yml"
ACTION = "alstr/todo-to-issue-action@64aca8fda7023259aada83ba44ad988c4c443657"


@pytest.fixture
def scanner_inputs() -> dict:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return next(
        step["with"] for step in workflow["jobs"]["scan"]["steps"] if step.get("uses") == ACTION
    )


@pytest.fixture
def python_language(scanner_inputs: dict) -> dict:
    definitions = json.loads((REPO_ROOT / scanner_inputs["LANGUAGES"]).read_text(encoding="utf-8"))
    assert [definition["language"] for definition in definitions] == ["Python"]
    return definitions[0]


def test_python_override_uses_only_hash_line_comments(python_language: dict) -> None:
    assert python_language["markers"] == [{"type": "line", "pattern": "#"}]


@pytest.mark.parametrize("identifier", ["TODO", "FIXME", "HACK"])
def test_python_markers_recognize_explicit_task_comments(
    python_language: dict, identifier: str
) -> None:
    comment = f"    # {identifier}(#889): scanner regression fixture"
    assert any(re.search(marker["pattern"], comment) for marker in python_language["markers"])


@pytest.mark.parametrize(
    "prose",
    [
        "Each agent maintains its own TODO list persisted at",
        "        agent_name: The agent creating this TODO (defaults to 'default')",
        '    """List TODO items for an agent.',
        "        todo_id: The ID of the TODO to complete",
        "        todo_id: The ID of the TODO to delete",
    ],
)
def test_python_markers_reject_reported_tool_docstrings(python_language: dict, prose: str) -> None:
    # This checks configured comment markers, not the external action's parser.
    assert not any(re.search(marker["pattern"], prose) for marker in python_language["markers"])


def test_python_override_keeps_existing_extension_coverage(python_language: dict) -> None:
    assert set(python_language["extensions"]) == {
        ".py",
        ".cgi",
        ".fcgi",
        ".gyp",
        ".gypi",
        ".lmi",
        ".py3",
        ".pyde",
        ".pyi",
        ".pyp",
        ".pyt",
        ".pyw",
        ".rpy",
        ".spec",
        ".tac",
        ".wsgi",
        ".xpy",
    }


def test_scanner_keeps_standard_languages_enabled(scanner_inputs: dict) -> None:
    assert scanner_inputs.get("NO_STANDARD", False) is False


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
