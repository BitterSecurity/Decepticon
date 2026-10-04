from __future__ import annotations

import hashlib
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


def planning_bundle_digest(workspace: str | Path) -> str:
    digest = hashlib.sha256()
    root = Path(workspace) / "plan"
    for filename in _PLANNING_DOCUMENTS:
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update((root / filename).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_planning_bundle(
    workspace: str | Path,
    *,
    target_value: str = "",
    authorization_confirmed: bool | None = None,
) -> str | None:
    """Return a failure reason unless the eight-document planner bundle is ready."""
    if authorization_confirmed is False:
        return "Authorization is not confirmed; do not mark the draft ready."

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
        return "RoE must contain an explicit authorization_reference before the draft is ready."
    if target_value and target_value not in {entry.target for entry in roe.in_scope}:
        return "RoE in_scope must contain the exact launcher-declared target."
    return None


def _runtime_context() -> tuple[str, str, bool | None]:
    try:
        configurable = get_config().get("configurable", {})
    except RuntimeError:
        configurable = {}
    if not isinstance(configurable, dict):
        configurable = {}
    workspace = configurable.get("workspace_path")
    target = configurable.get("target_value")
    confirmed = configurable.get("authorization_confirmed")
    return (
        workspace if isinstance(workspace, str) and workspace else "/workspace",
        target if isinstance(target, str) else "",
        confirmed if isinstance(confirmed, bool) else None,
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
    """Validate the full planner bundle and announce a reviewable draft.

    The event is emitted only after every Soundwave-owned document validates,
    the bundle shares an engagement name, the RoE records authorization, and
    any launcher-declared target appears exactly in RoE scope.
    """
    workspace, target_value, authorization_confirmed = _runtime_context()
    failure = validate_planning_bundle(
        workspace,
        target_value=target_value,
        authorization_confirmed=authorization_confirmed,
    )
    if failure:
        return f"Planning draft blocked: {failure}"
    try:
        root = Path(workspace)
        (root / ".planning-draft-ready").write_text(planning_bundle_digest(root), encoding="utf-8")
        (root / ".red-approved").unlink(missing_ok=True)
    except OSError as exc:
        return f"Planning draft could not be saved: {exc}"
    writer = _safe_writer()
    if writer is not None:
        writer(
            {
                "type": "planning_draft_ready",
                "agent": "soundwave",
                "id": tool_call_id,
            }
        )
    return "Planning draft ready. Review the documents in Interview mode; select Red explicitly after approval."
