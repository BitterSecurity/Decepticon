"""Tests for OPPLAN backend persistence, structured JSON, cycle guards, and the
strict-sequential ``after_model`` rule.

Covers the slice of the OPPLAN middleware that this PR introduced:

- ``_persist_opplan_to_backend`` writes a v1-shaped JSON document.
- Each mutating tool (add/update/expand/collapse) writes through the backend.
- ``_render`` and ``objective_collapse`` survive cycles in ``parent_id``.
- ``after_model`` rejects two OPPLAN tool calls in the same model step.
- ``list_objectives`` does not announce "ALL OBJECTIVES COMPLETE" for an
  empty plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.messages import AIMessage, ToolMessage

from decepticon.middleware import opplan as opplan_mod
from decepticon.middleware.filesystem import EngagementFilesystemBackend
from decepticon.middleware.opplan import OPPLANMiddleware
from decepticon.tools.opplan import (
    OPPLAN_FILE_SCHEMA_VERSION,
    OPPLAN_TOOL_NAMES,
    OPPLAN_VIRTUAL_PATH,
    _build_opplan_payload,
    _format_opplan_for_agent,
    _persist_opplan_to_backend,
)
from decepticon_core.types.engagement import OPPLAN, Objective, ObjectivePhase


def _obj_dict(obj_id: str, **overrides: Any) -> dict:
    base = {
        "id": obj_id,
        "title": f"objective {obj_id}",
        "phase": "recon",
        "description": "…",
        "acceptance_criteria": ["criterion"],
        "priority": 1,
        "status": "pending",
        "mitre": [],
        "opsec": "standard",
        "opsec_notes": "",
        "c2_tier": "interactive",
        "concessions": [],
        "blocked_by": [],
        "owner": "",
        "notes": "",
        "parent_id": None,
    }
    base.update(overrides)
    return base


# ── _build_opplan_payload / _persist_opplan_to_backend ─────────────────


def _backend(tmp_path: Path) -> FilesystemBackend:
    return FilesystemBackend(root_dir=tmp_path, virtual_mode=True)


def _opplan_path(tmp_path: Path, workspace_path: str = "/workspace") -> Path:
    rel = workspace_path.removeprefix("/").rstrip("/")
    return tmp_path / rel / "plan" / "opplan.json"


def test_build_opplan_payload_emits_versioned_envelope() -> None:
    opplan = OPPLAN(
        engagement_name="demo",
        threat_profile="apt-x",
        objectives=[
            Objective(
                id="OBJ-002",
                phase=ObjectivePhase.RECON,
                title="b",
                description="…",
                acceptance_criteria=["x"],
                priority=2,
            ),
            Objective(
                id="OBJ-001",
                phase=ObjectivePhase.RECON,
                title="a",
                description="…",
                acceptance_criteria=["x"],
                priority=1,
                status="completed",  # type: ignore[arg-type]
            ),
        ],
    )

    payload = _build_opplan_payload(opplan)

    assert payload["schema_version"] == OPPLAN_FILE_SCHEMA_VERSION
    assert payload["engagement_name"] == "demo"
    assert payload["threat_profile"] == "apt-x"
    # Objectives sorted by id for stable diffs.
    assert [o["id"] for o in payload["objectives"]] == ["OBJ-001", "OBJ-002"]
    # Summary aggregates by status.
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["completed"] == 1
    assert payload["summary"]["pending"] == 1


def test_persist_writes_payload_under_workspace_plan(tmp_path: Path) -> None:
    _persist_opplan_to_backend(
        _backend(tmp_path),
        "/workspace",
        [_obj_dict("OBJ-001", title="t")],
        engagement_name="demo",
        threat_profile="apt-x",
    )

    out = _opplan_path(tmp_path)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == OPPLAN_FILE_SCHEMA_VERSION
    assert data["engagement_name"] == "demo"
    assert [o["id"] for o in data["objectives"]] == ["OBJ-001"]


def test_persist_overwrites_existing_opplan_through_backend(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _persist_opplan_to_backend(
        backend,
        "/workspace",
        [_obj_dict("OBJ-001", status="pending")],
        engagement_name="demo",
        threat_profile="apt-x",
    )
    _persist_opplan_to_backend(
        backend,
        "/workspace",
        [_obj_dict("OBJ-001", status="completed", notes="evidence saved")],
        engagement_name="demo",
        threat_profile="apt-x",
    )

    data = json.loads(_opplan_path(tmp_path).read_text(encoding="utf-8"))
    assert data["objectives"][0]["status"] == "completed"
    assert data["objectives"][0]["notes"] == "evidence saved"


def test_persist_skips_when_workspace_path_missing(tmp_path: Path) -> None:
    # Should not raise; nothing should be written.
    _persist_opplan_to_backend(
        _backend(tmp_path),
        None,
        [_obj_dict("OBJ-001")],
        engagement_name="demo",
        threat_profile="apt-x",
    )
    _persist_opplan_to_backend(
        _backend(tmp_path),
        "",
        [_obj_dict("OBJ-001")],
        engagement_name="demo",
        threat_profile="apt-x",
    )
    assert list(tmp_path.iterdir()) == []


def test_persist_swallows_filesystem_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An OSError must become a log warning, not an exception."""

    def boom(self: Path, *a: Any, **kw: Any) -> None:
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", boom)
    # No assertion — just must not raise.
    _persist_opplan_to_backend(
        _backend(tmp_path),
        "/workspace",
        [_obj_dict("OBJ-001")],
        engagement_name="demo",
        threat_profile="apt-x",
    )


def test_loaded_opplan_ignores_persistence_metadata(tmp_path: Path) -> None:
    """``OPPLAN(**data)`` must drop the wrapper fields silently (Pydantic
    default extra-field policy) so ``load_opplan`` can read what we wrote."""
    _persist_opplan_to_backend(
        _backend(tmp_path),
        "/workspace",
        [_obj_dict("OBJ-001")],
        engagement_name="demo",
        threat_profile="apt-x",
    )
    data = json.loads(_opplan_path(tmp_path).read_text(encoding="utf-8"))
    # Sanity: the wrapper fields ARE present in the persisted file.
    assert {"schema_version", "saved_at", "summary"} <= set(data.keys())
    # And OPPLAN(**data) must round-trip without raising.
    opplan = OPPLAN(**data)
    assert opplan.engagement_name == "demo"
    assert [o.id for o in opplan.objectives] == ["OBJ-001"]


def test_load_opplan_reads_through_backend(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _persist_opplan_to_backend(
        backend,
        "/workspace",
        [_obj_dict("OBJ-001")],
        engagement_name="demo",
        threat_profile="apt-x",
    )

    cmd = _call(
        "load_opplan",
        {"workspace_path": "/workspace"},
        state={},
        backend=backend,
    )

    assert cmd.update["engagement_name"] == "demo"
    assert cmd.update["workspace_path"] == "/workspace"
    assert cmd.update["objectives"][0]["id"] == "OBJ-001"
    assert OPPLAN_VIRTUAL_PATH in cmd.update["messages"][0].content


def test_load_opplan_keeps_legacy_plan_with_graph_diagnostics(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    _persist_opplan_to_backend(
        backend,
        "/workspace",
        [_obj_dict("OBJ-001", blocked_by=["OBJ-999"])],
        engagement_name="demo",
        threat_profile="apt-x",
    )

    cmd = _call(
        "load_opplan",
        {"workspace_path": "/workspace"},
        state={},
        backend=backend,
    )

    assert cmd.update["objectives"][0]["id"] == "OBJ-001"
    assert "Graph integrity warnings: missing_dependency" in cmd.update["messages"][0].content


def test_load_opplan_binds_workspace_when_plan_is_missing(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    cmd = _call(
        "load_opplan",
        {"workspace_path": "/workspace"},
        state={},
        backend=backend,
    )

    assert cmd.update["workspace_path"] == "/workspace"
    assert "No opplan.json found" in cmd.update["messages"][0].content

    scoped = EngagementFilesystemBackend(backend, cmd.update["workspace_path"])
    result = scoped.write("/workspace/plan/roe.json", "{}")
    assert result.error is None


# ── auto-persist on each mutating tool ─────────────────────────────────


def _tool(name: str, backend=None):
    """Look up an OPPLAN tool by name from a fresh middleware instance."""
    tools = OPPLANMiddleware(backend=backend).tools
    return next(t for t in tools if t.name == name)


def _call(name: str, args: dict, state: dict, backend=None):
    """Invoke an OPPLAN tool with the LangChain ToolCall envelope.

    The tools declare ``tool_call_id: Annotated[str, InjectedToolCallId]``
    so they cannot be invoked with a plain kwargs dict — InjectedToolCallId
    must be injected through the ``{"type": "tool_call", "id": ...}`` shape.
    """
    payload = {
        "name": name,
        "type": "tool_call",
        "id": "test-call-id",
        "args": {**args, "state": state},
    }
    return _tool(name, backend=backend).invoke(payload)


def test_add_objective_auto_persists(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    cmd = _call(
        "add_objective",
        {
            "title": "scan",
            "phase": "recon",
            "description": "…",
            "acceptance_criteria": ["nmap output saved"],
            "priority": 1,
            "engagement_name": "demo",
            "threat_profile": "apt-x",
        },
        state={"workspace_path": "/workspace"},
        backend=backend,
    )
    assert _opplan_path(tmp_path).exists()
    assert cmd.update["objectives"][0]["title"] == "scan"


def test_update_objective_auto_persists(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    state = {
        "objectives": [_obj_dict("OBJ-001", status="pending")],
        "engagement_name": "demo",
        "threat_profile": "apt-x",
        "workspace_path": "/workspace",
    }
    _call(
        "update_objective",
        {"objective_id": "OBJ-001", "status": "in-progress"},
        state=state,
        backend=backend,
    )
    out = _opplan_path(tmp_path)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["objectives"][0]["status"] == "in-progress"


def test_objective_collapse_auto_persists(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    state = {
        "objectives": [
            _obj_dict("OBJ-001"),
            _obj_dict("OBJ-002", parent_id="OBJ-001"),
            _obj_dict("OBJ-003", parent_id="OBJ-002"),
        ],
        "engagement_name": "demo",
        "threat_profile": "apt-x",
        "workspace_path": "/workspace",
    }
    _call("objective_collapse", {"parent_id": "OBJ-001"}, state=state, backend=backend)
    out = _opplan_path(tmp_path)
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    statuses = {o["id"]: o["status"] for o in data["objectives"]}
    assert statuses["OBJ-002"] == "cancelled"
    assert statuses["OBJ-003"] == "cancelled"


# ── cycle protection ───────────────────────────────────────────────────


def test_objective_collapse_survives_parent_id_cycle() -> None:
    # OBJ-A.parent_id = OBJ-B, OBJ-B.parent_id = OBJ-A → cycle
    state = {
        "objectives": [
            _obj_dict("OBJ-A", parent_id="OBJ-B"),
            _obj_dict("OBJ-B", parent_id="OBJ-A"),
        ],
        "engagement_name": "demo",
        "threat_profile": "apt-x",
        "workspace_path": None,  # skip persistence
    }
    # Must terminate (no RecursionError, no hang).
    cmd = _call("objective_collapse", {"parent_id": "OBJ-A"}, state=state)
    cancelled = {o["id"] for o in cmd.update["objectives"] if o["status"] == "cancelled"}
    # OBJ-B is the descendant of OBJ-A; OBJ-A is itself the parent target.
    assert "OBJ-B" in cancelled


def test_format_opplan_for_agent_survives_parent_id_cycle() -> None:
    objectives = [
        _obj_dict("OBJ-A", parent_id="OBJ-B"),
        _obj_dict("OBJ-B", parent_id="OBJ-A"),
    ]
    # Must not infinite-recurse — the call returns in finite time.
    out = _format_opplan_for_agent(objectives, "demo", "apt-x")
    assert "## Task Tree" in out
    # The tree section appears once (we don't double-render the cycle).
    tree_section = out.split("## Task Tree", 1)[1]
    # Tree-line markers like "[ ]" should appear at most twice (once per node)
    # and never more — without the visited-set guard this would be unbounded.
    assert tree_section.count("[ ]") <= 2


# ── after_model strict-sequential ──────────────────────────────────────


def test_after_model_blocks_two_opplan_tools_in_same_step() -> None:
    middleware = OPPLANMiddleware()
    # Read tools must be blocked too (per the unified rule).
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-x", "name": "list_objectives", "args": {}, "type": "tool_call"},
            {
                "id": "tc-y",
                "name": "get_objective",
                "args": {"objective_id": "OBJ-1"},
                "type": "tool_call",
            },
        ],
    )
    update = middleware.after_model({"messages": [last_ai]}, runtime=None)
    assert update is not None
    msgs = update["messages"]
    # H14: first OPPLAN call is allowed, only 2nd+ are rejected
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage) and msgs[0].status == "error"
    assert msgs[0].tool_call_id == "tc-y"


def test_after_model_allows_single_opplan_tool() -> None:
    middleware = OPPLANMiddleware()
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-z", "name": "add_objective", "args": {}, "type": "tool_call"},
        ],
    )
    assert middleware.after_model({"messages": [last_ai]}, runtime=None) is None


def test_after_model_allows_opplan_alongside_non_opplan_tool() -> None:
    """One OPPLAN call plus an unrelated tool (e.g. bash) is fine."""
    middleware = OPPLANMiddleware()
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-1", "name": "add_objective", "args": {}, "type": "tool_call"},
            {"id": "tc-2", "name": "bash", "args": {"command": "ls"}, "type": "tool_call"},
        ],
    )
    assert middleware.after_model({"messages": [last_ai]}, runtime=None) is None


def test_after_model_rejects_task_parallel_with_plan_mutation() -> None:
    middleware = OPPLANMiddleware()
    last_ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "tc-plan", "name": "update_objective", "args": {}, "type": "tool_call"},
            {"id": "tc-task", "name": "task", "args": {}, "type": "tool_call"},
        ],
    )

    update = middleware.after_model({"messages": [last_ai]}, runtime=None)

    assert update is not None
    assert len(update["messages"]) == 1
    assert update["messages"][0].tool_call_id == "tc-task"
    assert update["messages"][0].status == "error"


def test_opplan_tool_names_constant_matches_registered_tools() -> None:
    registered = {t.name for t in OPPLANMiddleware().tools}
    assert registered == set(OPPLAN_TOOL_NAMES)


# ── empty objectives renders correctly ─────────────────────────────────


def test_format_opplan_status_empty_does_not_say_all_complete() -> None:
    out = opplan_mod._format_opplan_status([], "demo", "apt-x")
    assert "ALL OBJECTIVES COMPLETE" not in out
    assert "No objectives defined" in out


def test_format_opplan_for_agent_empty_does_not_say_all_complete() -> None:
    """Regression: empty objectives list must not report all-done (vacuous all())."""
    out = _format_opplan_for_agent([], "demo", "apt-x")
    assert "ALL OBJECTIVES COMPLETE" not in out
