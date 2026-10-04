"""complete_engagement_planning — validate and signal the planning handoff."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId, tool
from langgraph.config import get_config, get_stream_writer

from decepticon_core.types.engagement import (
    CONOPS,
    AbortPlan,
    CleanupPlan,
    ContactPlan,
    DataHandlingPlan,
    DeconflictionPlan,
    RoE,
    ThreatProfile,
)

_PLANNING_DOCUMENTS = {
    "roe.json": RoE,
    "threat-profile.json": ThreatProfile,
    "conops.json": CONOPS,
    "deconfliction.json": DeconflictionPlan,
    "contact.json": ContactPlan,
    "data-handling.json": DataHandlingPlan,
    "abort.json": AbortPlan,
    "cleanup.json": CleanupPlan,
}


def validate_planning_bundle(
    workspace: str | Path,
    *,
    target_value: str = "",
    authorization_confirmed: bool | None = None,
) -> str | None:
    """Return a failure reason unless the eight-document planner bundle is ready."""
    if authorization_confirmed is False:
        return "Authorization is not confirmed; do not hand off the engagement."

    root = Path(workspace)
    documents: dict[str, Any] = {}
    for filename, model in _PLANNING_DOCUMENTS.items():
        path = root / "plan" / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            documents[filename] = model.model_validate(payload)
        except FileNotFoundError:
            return f"Missing required planning document: plan/{filename}."
        except (json.JSONDecodeError, ValueError) as exc:
            return f"Invalid planning document plan/{filename}: {exc}"

    names = {document.engagement_name.strip() for document in documents.values()}
    if len(names) != 1 or not next(iter(names), ""):
        return "Planning documents must share one non-empty engagement_name."

    roe = documents["roe.json"]
    if not roe.authorization_reference.strip():
        return "RoE must contain an explicit authorization_reference before handoff."
    if target_value and target_value not in {entry.target for entry in roe.in_scope}:
        return "RoE in_scope must contain the exact launcher-declared target."
    return None


def _runtime_context() -> tuple[str, str, bool | None, bool]:
    try:
        configurable = get_config().get("configurable", {})
    except RuntimeError:
        configurable = {}
    if not isinstance(configurable, dict):
        configurable = {}
    workspace = configurable.get("workspace_path")
    target = configurable.get("target_value")
    confirmed = configurable.get("authorization_confirmed")
    planning_draft = configurable.get("planning_draft") is True
    return (
        workspace if isinstance(workspace, str) and workspace else "/workspace",
        target if isinstance(target, str) else "",
        confirmed if isinstance(confirmed, bool) else None,
        planning_draft,
    )


def _safe_writer():
    try:
        return get_stream_writer()
    except Exception:
        return None


@tool
def complete_engagement_planning(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Any:
    """Validate the full planner bundle and hand the engagement to Decepticon.

    The event is emitted only after every Soundwave-owned document validates,
    the bundle shares an engagement name, the RoE records authorization, and
    any launcher-declared target appears exactly in RoE scope.
    """
    workspace, target_value, authorization_confirmed, planning_draft = _runtime_context()
    failure = validate_planning_bundle(
        workspace,
        target_value=target_value,
        authorization_confirmed=authorization_confirmed,
    )
    if failure:
        return f"Planning handoff blocked: {failure}"
    writer = _safe_writer()
    if writer is not None:
        writer(
            {
                "type": "engagement_ready",
                "agent": "soundwave",
                "id": tool_call_id,
            }
        )
    if planning_draft:
        return "Next-run planning draft saved. The current Red authorization is unchanged."
    return "Planning complete. Review and approve the documents, then select Red to launch."
