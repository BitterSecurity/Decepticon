"""Tests for the OPPLAN hierarchy / Pentesting Task Tree (PTT) feature."""

from __future__ import annotations

import json
from typing import Any

import pytest

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


# ── Middleware tools ───────────────────────────────────────────────────


class _ToolBag:
    """Convenience accessor over the OPPLAN tool list."""

    def __init__(self) -> None:
        self.tools = build_opplan_tools()
        by_name = {getattr(t, "name", None) or t.__name__: t for t in self.tools}
        self.add = by_name["add_objective"]
        self.update = by_name["update_objective"]
        self.list = by_name["list_objectives"]
        self.expand = by_name["objective_expand"]
        self.collapse = by_name["objective_collapse"]


def _state_from(command: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Apply a Command's state update on top of the current state copy."""
    new_state = {**state}
    update = getattr(command, "update", None) or {}
    for key, value in update.items():
        if key == "messages":
            continue
        new_state[key] = value
    return new_state


def _last_message(command: Any) -> str:
    update = getattr(command, "update", None) or {}
    messages = update.get("messages") or []
    if not messages:
        return ""
    msg = messages[-1]
    return getattr(msg, "content", str(msg))


def _call_tool(tool: Any, args: dict[str, Any], state: dict[str, Any]) -> Any:
    """Invoke a middleware tool with a synthetic ToolCall envelope.

    The middleware tools declare ``tool_call_id: Annotated[str, InjectedToolCallId]``
    — LangChain requires that to be injected via a ``{"type": "tool_call",
    "tool_call_id": ...}`` wrapper rather than passed as a plain kwarg.
    """
    payload = {
        "name": getattr(tool, "name", "tool"),
        "type": "tool_call",
        "id": "test-call-id",
        "args": {**args, "state": state},
    }
    return tool.invoke(payload)


@pytest.fixture
def bag() -> _ToolBag:
    return _ToolBag()


@pytest.fixture
def initial_state() -> dict[str, Any]:
    return {"objectives": [], "objective_counter": 0}


def _add(bag: _ToolBag, state: dict, **kwargs: Any) -> dict:
    cmd = _call_tool(bag.add, kwargs, state)
    return _state_from(cmd, state)


def _expand(bag: _ToolBag, state: dict, parent_id: str, children: list[dict]) -> tuple[dict, Any]:
    cmd = _call_tool(
        bag.expand,
        {"parent_id": parent_id, "children": children},
        state,
    )
    return _state_from(cmd, state), cmd


class TestAddObjectiveWithParent:
    def test_parent_must_exist(self, bag: _ToolBag, initial_state: dict) -> None:
        cmd = _call_tool(
            bag.add,
            {
                "title": "Child",
                "phase": ObjectivePhase.RECON,
                "description": "x",
                "acceptance_criteria": ["c"],
                "priority": 1,
                "parent_id": "OBJ-999",
            },
            initial_state,
        )
        assert "not found" in _last_message(cmd)

    def test_add_with_valid_parent(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="r",
            acceptance_criteria=["c"],
            priority=1,
        )
        s = _add(
            bag,
            s,
            title="Child",
            phase=ObjectivePhase.RECON,
            description="ch",
            acceptance_criteria=["c"],
            priority=2,
            parent_id="OBJ-001",
        )
        objs = s["objectives"]
        assert len(objs) == 2
        child = next(o for o in objs if o["id"] == "OBJ-002")
        assert child["parent_id"] == "OBJ-001"


class TestObjectiveExpand:
    def test_creates_children(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Compromise AD",
            phase=ObjectivePhase.POST_EXPLOIT,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        s, cmd = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[
                {
                    "title": "Pivot via SOCKS",
                    "description": "Stand up chisel",
                    "acceptance_criteria": ["socks running"],
                },
                {
                    "title": "Re-scan internal subnet",
                    "description": "rustscan",
                    "acceptance_criteria": ["scan complete"],
                },
            ],
        )
        assert len(s["objectives"]) == 3
        assert "Expanded OBJ-001" in _last_message(cmd)
        kids = [o for o in s["objectives"] if o.get("parent_id") == "OBJ-001"]
        assert len(kids) == 2

    def test_expand_unknown_parent(self, bag: _ToolBag, initial_state: dict) -> None:
        _, cmd = _expand(
            bag,
            initial_state,
            parent_id="OBJ-999",
            children=[{"title": "x", "description": "y", "acceptance_criteria": ["z"]}],
        )
        assert "not found" in _last_message(cmd)

    def test_expand_completed_parent_rejected(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        # Manually flip status (avoids parent-rollup blocking us)
        s["objectives"][0]["status"] = "completed"
        _, cmd = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[{"title": "x", "description": "y", "acceptance_criteria": ["z"]}],
        )
        assert "Cannot expand" in _last_message(cmd)

    def test_expand_empty_children_rejected(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        _, cmd = _expand(bag, s, parent_id="OBJ-001", children=[])
        assert "empty" in _last_message(cmd).lower()


class TestParentCompletionGuard:
    def test_parent_cannot_complete_with_open_children(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        s, _ = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[{"title": "Child", "description": "y", "acceptance_criteria": ["z"]}],
        )
        # Move parent to in-progress
        s = _state_from(
            _call_tool(
                bag.update,
                {"objective_id": "OBJ-001", "status": "in-progress"},
                s,
            ),
            s,
        )
        cmd = _call_tool(
            bag.update,
            {"objective_id": "OBJ-001", "status": "completed"},
            s,
        )
        msg = _last_message(cmd)
        assert "Cannot complete OBJ-001" in msg
        assert "OBJ-002" in msg

    def test_parent_completes_after_child_done(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        s, _ = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[{"title": "Child", "description": "y", "acceptance_criteria": ["z"]}],
        )
        # Drive both objectives to completed
        for obj_id in ("OBJ-001", "OBJ-002"):
            s = _state_from(
                _call_tool(
                    bag.update,
                    {"objective_id": obj_id, "status": "in-progress"},
                    s,
                ),
                s,
            )
        # Complete child first, then parent.
        s = _state_from(
            _call_tool(
                bag.update,
                {"objective_id": "OBJ-002", "status": "completed"},
                s,
            ),
            s,
        )
        cmd = _call_tool(
            bag.update,
            {"objective_id": "OBJ-001", "status": "completed"},
            s,
        )
        s = _state_from(cmd, s)
        statuses = {o["id"]: o["status"] for o in s["objectives"]}
        assert statuses["OBJ-001"] == "completed"
        assert statuses["OBJ-002"] == "completed"


class TestObjectiveCollapse:
    def test_collapse_cancels_descendants(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
        )
        s, _ = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[
                {"title": "A", "description": "x", "acceptance_criteria": ["c"]},
                {"title": "B", "description": "x", "acceptance_criteria": ["c"]},
            ],
        )
        cmd = _call_tool(bag.collapse, {"parent_id": "OBJ-001"}, s)
        s = _state_from(cmd, s)
        statuses = {o["id"]: o["status"] for o in s["objectives"]}
        assert statuses["OBJ-002"] == "cancelled"
        assert statuses["OBJ-003"] == "cancelled"
        # Parent untouched
        assert statuses["OBJ-001"] == "pending"

    def test_collapse_unknown_parent(self, bag: _ToolBag, initial_state: dict) -> None:
        cmd = _call_tool(bag.collapse, {"parent_id": "OBJ-999"}, initial_state)
        assert "not found" in _last_message(cmd)


class TestListWithTree:
    def test_renders_tree_view(self, bag: _ToolBag, initial_state: dict) -> None:
        s = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="x",
            acceptance_criteria=["c"],
            priority=1,
            engagement_name="op",
            threat_profile="t",
        )
        s, _ = _expand(
            bag,
            s,
            parent_id="OBJ-001",
            children=[{"title": "Pivot", "description": "x", "acceptance_criteria": ["c"]}],
        )
        cmd = _call_tool(bag.list, {}, s)
        msg = _last_message(cmd)
        assert "Task Tree" in msg
        assert "OBJ-001" in msg
        assert "OBJ-002" in msg
        # Indented child marker
        assert "↳" in msg or "- [" in msg

    def test_next_objective_uses_ready_frontier(self, bag: _ToolBag, initial_state: dict) -> None:
        state = _add(
            bag,
            initial_state,
            title="Prerequisite",
            phase=ObjectivePhase.RECON,
            description="Inspect scoped service",
            acceptance_criteria=["Observation recorded"],
            priority=2,
        )
        state = _add(
            bag,
            state,
            title="Dependent",
            phase=ObjectivePhase.RECON,
            description="Test permission boundary",
            acceptance_criteria=["Boundary tested"],
            priority=1,
            blocked_by=["OBJ-001"],
        )

        cmd = _call_tool(bag.list, {}, state)

        assert "Next: OBJ-001" in _last_message(cmd)
        assert "Status-ready candidates: OBJ-001" in _last_message(cmd)


class TestObjectiveGraphGuards:
    def test_add_rejects_child_of_completed_parent(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="Parent",
            phase=ObjectivePhase.RECON,
            description="Inspect surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        state["objectives"][0]["status"] = "completed"

        cmd = _call_tool(
            bag.add,
            {
                "title": "Child",
                "phase": ObjectivePhase.RECON,
                "description": "Inspect child surface",
                "acceptance_criteria": ["Observation recorded"],
                "priority": 2,
                "parent_id": "OBJ-001",
            },
            state,
        )

        assert "Cannot add a child to completed" in _last_message(cmd)
        assert "objectives" not in cmd.update

    def test_add_rejects_missing_dependency(self, bag: _ToolBag, initial_state: dict) -> None:
        cmd = _call_tool(
            bag.add,
            {
                "title": "Recon",
                "phase": ObjectivePhase.RECON,
                "description": "Inspect service",
                "acceptance_criteria": ["Observation recorded"],
                "priority": 1,
                "blocked_by": ["OBJ-999"],
            },
            initial_state,
        )

        assert "missing_dependency" in _last_message(cmd)
        assert "objectives" not in cmd.update

    def test_update_rejects_start_and_new_unresolved_dependency_together(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="First",
            phase=ObjectivePhase.RECON,
            description="Inspect first surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        state = _add(
            bag,
            state,
            title="Second",
            phase=ObjectivePhase.RECON,
            description="Inspect second surface",
            acceptance_criteria=["Observation recorded"],
            priority=2,
        )
        cmd = _call_tool(
            bag.update,
            {
                "objective_id": "OBJ-001",
                "status": "in-progress",
                "add_blocked_by": ["OBJ-002"],
            },
            state,
        )

        assert "blocked by unresolved objectives: OBJ-002" in _last_message(cmd)
        assert "objectives" not in cmd.update

    def test_update_rejects_new_dependency_on_running_objective(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="First",
            phase=ObjectivePhase.RECON,
            description="Inspect first surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        state = _add(
            bag,
            state,
            title="Second",
            phase=ObjectivePhase.RECON,
            description="Inspect second surface",
            acceptance_criteria=["Observation recorded"],
            priority=2,
        )
        state = _state_from(
            _call_tool(
                bag.update,
                {"objective_id": "OBJ-001", "status": "in-progress"},
                state,
            ),
            state,
        )

        cmd = _call_tool(
            bag.update,
            {"objective_id": "OBJ-001", "add_blocked_by": ["OBJ-002"]},
            state,
        )

        assert "blocked by unresolved objectives: OBJ-002" in _last_message(cmd)
        assert "objectives" not in cmd.update

    def test_update_rejects_completion_with_unresolved_dependency(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="First",
            phase=ObjectivePhase.RECON,
            description="Inspect first surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        state = _add(
            bag,
            state,
            title="Second",
            phase=ObjectivePhase.RECON,
            description="Inspect second surface",
            acceptance_criteria=["Observation recorded"],
            priority=2,
        )
        state["objectives"][0]["status"] = "blocked"

        cmd = _call_tool(
            bag.update,
            {
                "objective_id": "OBJ-001",
                "status": "completed",
                "add_blocked_by": ["OBJ-002"],
            },
            state,
        )

        assert (
            "Cannot set OBJ-001 to completed: blocked by unresolved objectives: OBJ-002"
            in _last_message(cmd)
        )
        assert "objectives" not in cmd.update

    def test_update_rejects_cycle_before_persisting(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="First",
            phase=ObjectivePhase.RECON,
            description="Inspect first surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        state = _add(
            bag,
            state,
            title="Second",
            phase=ObjectivePhase.RECON,
            description="Inspect second surface",
            acceptance_criteria=["Observation recorded"],
            priority=2,
            blocked_by=["OBJ-001"],
        )
        cmd = _call_tool(
            bag.update,
            {"objective_id": "OBJ-001", "add_blocked_by": ["OBJ-002"]},
            state,
        )

        assert "dependency_cycle" in _last_message(cmd)
        assert "objectives" not in cmd.update

    def test_expand_rejects_unknown_child_dependency(
        self, bag: _ToolBag, initial_state: dict
    ) -> None:
        state = _add(
            bag,
            initial_state,
            title="Root",
            phase=ObjectivePhase.RECON,
            description="Inspect surface",
            acceptance_criteria=["Observation recorded"],
            priority=1,
        )
        _, cmd = _expand(
            bag,
            state,
            parent_id="OBJ-001",
            children=[
                {
                    "title": "Child",
                    "description": "Inspect child surface",
                    "acceptance_criteria": ["Observation recorded"],
                    "blocked_by": ["OBJ-999"],
                }
            ],
        )

        assert "missing_dependency" in _last_message(cmd)
        assert "objectives" not in cmd.update


# Suppress unused imports
_ = json
