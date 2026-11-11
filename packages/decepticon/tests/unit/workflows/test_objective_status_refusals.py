"""What the agent is told when ``update_objective`` refuses a status change.

Runs the registered OPPLAN tool against a real filesystem backend. A refusal
from a final status used to list no options at all; it now names the only
legitimate way forward, while non-final refusals keep their existing message.
"""

from __future__ import annotations

import json
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from langgraph.types import Command

from decepticon.tools.opplan import _persist_opplan_to_backend, build_opplan_tools
from decepticon_core.types.engagement import ObjectiveStatus

from .opplan_helpers import objective, state_for


def _request_status(tmp_path: Path, current: ObjectiveStatus, requested: str) -> tuple[str, str]:
    backend = FilesystemBackend(root_dir=tmp_path, virtual_mode=True)
    target = objective("target", "analyze", "reverser", current)
    state = state_for([target])
    assert (
        _persist_opplan_to_backend(
            backend, "/workspace", state["objectives"], "test", "", revision=1
        )
        is None
    )
    tool = next(tool for tool in build_opplan_tools(backend) if tool.name == "update_objective")
    result = tool.invoke(
        {
            "type": "tool_call",
            "name": tool.name,
            "id": "change",
            "args": {"state": state, "objective_id": target.id, "status": requested},
        }
    )
    assert isinstance(result, Command)
    assert result.update is not None
    message = result.update["messages"][0]
    assert message.status == "error"
    stored = json.loads((tmp_path / "workspace/plan/opplan.json").read_text())
    return str(message.content), stored["objectives"][0]["status"]


def test_completed_refusal_points_to_fact_revocation(tmp_path: Path) -> None:
    content, stored = _request_status(tmp_path, ObjectiveStatus.COMPLETED, "in-progress")
    assert content.startswith("Invalid transition: completed → in-progress. ")
    assert "'completed' is final" in content
    assert "revoke_plan_fact" in content
    assert stored == "completed"


def test_cancelled_refusal_points_to_a_new_objective(tmp_path: Path) -> None:
    content, stored = _request_status(tmp_path, ObjectiveStatus.CANCELLED, "in-progress")
    assert content.startswith("Invalid transition: cancelled → in-progress. ")
    assert "'cancelled' is final" in content
    assert "commit_opplan" in content
    assert stored == "cancelled"


def test_non_final_refusal_keeps_listing_the_allowed_statuses(tmp_path: Path) -> None:
    # (Requesting ``completed`` would hit the earlier outcome gate instead.)
    content, stored = _request_status(tmp_path, ObjectiveStatus.IN_PROGRESS, "pending")
    assert content == (
        "Invalid transition: in-progress → pending. "
        "Valid from 'in-progress': blocked, cancelled, completed"
    )
    assert stored == "in-progress"
