"""Tests for the OPPLAN hierarchy / Pentesting Task Tree (PTT) feature."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from deepagents.backends.filesystem import FilesystemBackend

from decepticon.tools.opplan import build_opplan_tools
from decepticon_core.types.engagement import (
    OPPLAN,
    Objective,
    ObjectivePhase,
    ObjectiveStatus,
)


def _objective(
    obj_id: str,
    title: str,
    *,
    parent_id: str | None = None,
    priority: int = 1,
    status: ObjectiveStatus = ObjectiveStatus.PENDING,
    phase: ObjectivePhase = ObjectivePhase.RECON,
) -> Objective:
    return Objective(
        id=obj_id,
        title=title,
        phase=phase,
        description="…",
        acceptance_criteria=["criterion"],
        priority=priority,
        status=status,
        parent_id=parent_id,
    )


# ── Schema helpers ─────────────────────────────────────────────────────


class TestSchemaHierarchy:
    def test_children_of(self) -> None:
        plan = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root"),
                _objective("OBJ-002", "child-a", parent_id="OBJ-001"),
                _objective("OBJ-003", "child-b", parent_id="OBJ-001"),
                _objective("OBJ-004", "grandchild", parent_id="OBJ-002"),
            ],
        )
        kids = plan.children_of("OBJ-001")
        assert {k.id for k in kids} == {"OBJ-002", "OBJ-003"}

    def test_descendants_of(self) -> None:
        plan = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root"),
                _objective("OBJ-002", "child-a", parent_id="OBJ-001"),
                _objective("OBJ-003", "grandchild", parent_id="OBJ-002"),
            ],
        )
        d = plan.descendants_of("OBJ-001")
        assert {x.id for x in d} == {"OBJ-002", "OBJ-003"}

    def test_root_objectives(self) -> None:
        plan = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root1"),
                _objective("OBJ-002", "child", parent_id="OBJ-001"),
                _objective("OBJ-003", "root2"),
            ],
        )
        roots = plan.root_objectives()
        assert {r.id for r in roots} == {"OBJ-001", "OBJ-003"}

    def test_has_hierarchy(self) -> None:
        flat = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[_objective("OBJ-001", "root")],
        )
        assert flat.has_hierarchy() is False
        nested = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root"),
                _objective("OBJ-002", "child", parent_id="OBJ-001"),
            ],
        )
        assert nested.has_hierarchy() is True

    def test_detect_cycle(self) -> None:
        plan = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root"),
                _objective("OBJ-002", "child", parent_id="OBJ-001"),
                _objective("OBJ-003", "grandchild", parent_id="OBJ-002"),
            ],
        )
        # Attaching root under grandchild would create a cycle
        assert plan.detect_cycle("OBJ-001", "OBJ-003") is True
        # Attaching a sibling under root is fine
        assert plan.detect_cycle("OBJ-004", "OBJ-001") is False
        # Self-parenting is a cycle
        assert plan.detect_cycle("OBJ-001", "OBJ-001") is True

    def test_tree_returns_nested_dicts(self) -> None:
        plan = OPPLAN(
            engagement_name="e",
            threat_profile="t",
            objectives=[
                _objective("OBJ-001", "root", priority=1),
                _objective("OBJ-002", "child-a", parent_id="OBJ-001", priority=1),
                _objective("OBJ-003", "child-b", parent_id="OBJ-001", priority=2),
            ],
        )
        tree = plan.tree()
        assert len(tree) == 1
        root = tree[0]
        assert root["id"] == "OBJ-001"
        children = root["children"]
        assert isinstance(children, list)
        assert len(children) == 2
        assert children[0]["id"] == "OBJ-002"


def _invoke(tool: Any, args: dict[str, Any], state: dict[str, Any]) -> Any:
    return tool.invoke(
        {
            "name": tool.name,
            "type": "tool_call",
            "id": "test-call-id",
            "args": {**args, "state": state},
        }
    )


def _row(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "title": title,
        "phase": "recon",
        "description": title,
        "acceptance_criteria": ["Result recorded"],
        "priority": 1,
        **extra,
    }


def test_agent_toolset_exposes_only_dag_plan_mutations() -> None:
    names = {tool.name for tool in build_opplan_tools()}
    assert {"commit_opplan", "update_objective", "record_plan_fact", "revoke_plan_fact"} <= names
    assert {"add_objective", "objective_expand", "objective_collapse"}.isdisjoint(names)


def test_commit_preserves_hierarchy_and_reports_ready_leaf(tmp_path: Path) -> None:
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    tools = {tool.name: tool for tool in build_opplan_tools(backend)}
    command = _invoke(
        tools["commit_opplan"],
        {
            "objectives": [
                _row("Parent"),
                _row("Independent leaf", parent_id=0),
                _row("Dependent leaf", parent_id=0, blocked_by=[1]),
            ],
            "expected_revision": 0,
            "engagement_name": "demo",
        },
        {"workspace_path": "/workspace", "objectives": [], "plan_revision": 0},
    )
    assert command.update["plan_revision"] == 1
    parent, first, second = command.update["objectives"]
    UUID(parent["id"])
    UUID(first["id"])
    UUID(second["id"])
    assert first["parent_id"] == parent["id"]
    assert second["blocked_by"] == [first["id"]]
    assert first["id"] in command.update["messages"][0].content
    assert second["id"] not in command.update["messages"][0].content.split("Status-ready: ")[1]
    state = {
        "workspace_path": "/workspace",
        **{key: value for key, value in command.update.items() if key != "messages"},
    }
    listed = _invoke(tools["list_objectives"], {}, state)
    assert "Task Tree" in listed.update["messages"][0].content
    assert f"Next: {first['id']}" in listed.update["messages"][0].content


def test_commit_rejects_parent_cycle(tmp_path: Path) -> None:
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "commit_opplan")
    command = _invoke(
        tool,
        {
            "objectives": [_row("A", parent_id=1), _row("B", parent_id=0)],
            "expected_revision": 0,
        },
        {"workspace_path": "/workspace", "objectives": [], "plan_revision": 0},
    )
    assert command.update["messages"][0].status == "error"
    assert "cycle" in command.update["messages"][0].content.lower()
    assert "objectives" not in command.update
