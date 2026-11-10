from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Any, Protocol

from langchain.tools import ToolRuntime
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


class PlanningDocumentSource(Protocol):
    """Trusted client-owned, read-only source for fixed planning filenames.

    Revalidate current actor/run authority on each read. Revisions must identify
    immutable content; this interface never admits target execution or writes RoE.
    """

    def read_document(self, filename: str) -> tuple[bytes, str]: ...


PlanningDocumentSourceProvider = Callable[[ToolRuntime], PlanningDocumentSource | None]


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
    failures: list[str] = []
    for filename, model in _PLANNING_DOCUMENTS.items():
        path = root / "plan" / filename
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            documents[filename] = model.model_validate(payload)
        except FileNotFoundError:
            failures.append(f"Missing required planning document: plan/{filename}.")
        except (json.JSONDecodeError, ValueError) as exc:
            failures.append(f"Invalid planning document plan/{filename}: {exc}")

    if failures:
        return "\n".join(failures)

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


def create_complete_planning_tool(source_provider: PlanningDocumentSourceProvider):
    """Keep native validation/event ownership while adapting document storage.

    The provider is injected by trusted factory code, never deserialized from
    graph configuration or model input. Returning None preserves local behavior;
    provider/read failures are refused, with no fallback to host files.
    """

    @tool("complete_engagement_planning")
    def complete_from_source(
        runtime: ToolRuntime,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Any:
        """Validate the eight-document planning bundle and announce operator review."""
        try:
            source = source_provider(runtime)
            if source is None:
                return complete_engagement_planning.func(tool_call_id=tool_call_id)
            configurable = (runtime.config or {}).get("configurable", {})
            revisions: dict[str, str] = {}
            # The upstream validator is Path-based. Only fixed, bounded files
            # enter this private per-call adapter; it is never an agent workspace.
            with TemporaryDirectory(prefix="decepticon-planning-validation-") as temporary:
                root = Path(temporary)
                (root / "plan").mkdir(mode=0o700)
                for filename in _PLANNING_DOCUMENTS:
                    content, revision = source.read_document(filename)
                    if not isinstance(content, bytes) or len(content) > 512 * 1024:
                        raise ValueError("Invalid planning document size")
                    if not isinstance(revision, str) or not revision or len(revision) > 256:
                        raise ValueError("Missing planning document revision")
                    content.decode("utf-8")
                    (root / "plan" / filename).write_bytes(content)
                    revisions[filename] = revision
                target = configurable.get("target_value")
                confirmed = configurable.get("authorization_confirmed")
                failure = validate_planning_bundle(
                    root,
                    target_value=target if isinstance(target, str) else "",
                    authorization_confirmed=confirmed if isinstance(confirmed, bool) else None,
                )
                if failure:
                    return f"Planning draft blocked: {failure}"
                digest = planning_bundle_digest(root)
                for filename, expected in revisions.items():
                    content, revision = source.read_document(filename)
                    if revision != expected or content != (root / "plan" / filename).read_bytes():
                        return "Planning documents changed during validation; review and validate again."
            writer = _safe_writer()
            if writer is not None:
                writer(
                    {
                        "type": "planning_draft_ready",
                        "agent": "soundwave",
                        "id": tool_call_id,
                        "bundle_digest": digest,
                    }
                )
            return "Planning draft ready. Review the documents in Plan mode; select Red explicitly when ready."
        except Exception:
            # Storage credentials, transport URLs and underlying exception
            # payloads must not become model-visible tool responses.
            return "Planning draft validation could not be confirmed; reread the documents before retrying."

    return complete_from_source


@tool
def complete_engagement_planning(
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> Any:
    """Validate the full planner bundle and announce a reviewable draft.

    The event is emitted only after the seven planning documents and the
    system-provided RoE validate, the bundle shares an engagement name, and
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
    return (
        "Planning draft ready. Review the documents in Plan mode; select Red explicitly when ready."
    )
