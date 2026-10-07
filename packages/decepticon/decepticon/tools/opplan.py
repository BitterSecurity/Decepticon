"""OPPLAN tools and backend persistence helpers."""

from __future__ import annotations

import json
import logging
import posixpath
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from deepagents.backends.protocol import BackendProtocol
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from decepticon.tools.opplan_graph import PlanInspection, inspect_plan, unmet_prerequisites
from decepticon_core.types.attack_catalog import canonical_attack_annotation
from decepticon_core.types.engagement import (
    OPPLAN,
    C2Tier,
    Objective,
    ObjectiveOutcome,
    ObjectivePhase,
    ObjectiveStatus,
    OpsecLevel,
    PlanFact,
)

log = logging.getLogger(__name__)
_run_sandbox_backend: ContextVar[BackendProtocol | None] = ContextVar(
    "opplan_run_sandbox_backend", default=None
)

OPPLAN_FILE_SCHEMA_VERSION = "2"
OPPLAN_VIRTUAL_PATH = "/workspace/plan/opplan.json"
_UNSET = object()

# All OPPLAN tools — used by ``OPPLANMiddleware.after_model`` to enforce
# strictly sequential calls (one OPPLAN tool per LLM step). Parallel calls
# would either race on ``state.objectives`` (mutations) or just waste a
# round-trip (reads), so the same rule applies uniformly.
OPPLAN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "add_objective",
        "update_objective",
        "get_objective",
        "list_objectives",
        "objective_expand",
        "objective_collapse",
        "load_opplan",
        "commit_opplan",
        "record_plan_fact",
        "revoke_plan_fact",
    }
)

_VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in-progress", "cancelled"},
    "in-progress": {"completed", "blocked", "cancelled"},
    "blocked": {"in-progress", "completed", "cancelled"},  # retry, abandon, drop
    # completed is terminal
    # cancelled is terminal
}


def _inspect_objective_rows(
    objectives: list[dict[str, Any]], facts: list[dict[str, Any]] | None = None
) -> PlanInspection:
    return inspect_plan(
        [Objective.model_validate(row) for row in objectives],
        [PlanFact.model_validate(row) for row in (facts or [])],
    )


def _graph_rejection(inspection: PlanInspection, tool_call_id: str) -> Command[Any]:
    details = "; ".join(issue.describe() for issue in inspection.issues)
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Invalid OPPLAN graph: {details}",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            ]
        }
    )


def _assign_objective_ids(
    objectives: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    existing_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, str]]:
    ids: list[str] = []
    issued: dict[int, str] = {}
    for index, row in enumerate(objectives):
        if not isinstance(row, dict):
            raise ValueError(f"Objective at position {index} must be an object")
        objective_id = row.get("id")
        if objective_id is None:
            objective_id = str(uuid4())
            issued[index] = objective_id
        elif not isinstance(objective_id, str) or objective_id not in existing_ids:
            raise ValueError(
                f"Objective at position {index} supplies an unknown ID; "
                "omit id for a new node so the server can issue it"
            )
        ids.append(objective_id)

    def resolve(reference: Any) -> str:
        if type(reference) is int:
            if reference < 0 or reference >= len(ids):
                raise ValueError(f"Objective position {reference} is outside this DAG submission")
            return ids[reference]
        if isinstance(reference, str):
            return reference
        raise ValueError(f"Invalid objective reference {reference!r}; use an ID or array position")

    resolved: list[dict[str, Any]] = []
    for index, row in enumerate(objectives):
        item = {**row, "id": ids[index]}
        item["blocked_by"] = [resolve(ref) for ref in row.get("blocked_by", [])]
        item["any_of"] = [[resolve(ref) for ref in group] for group in row.get("any_of", [])]
        if row.get("parent_id") is not None:
            item["parent_id"] = resolve(row["parent_id"])
        resolved.append(item)
    resolved_facts = [
        {**fact, "producer_id": resolve(fact["producer_id"])}
        if "producer_id" in fact
        else dict(fact)
        for fact in facts
    ]
    return resolved, resolved_facts, issued


def _canonicalize_attack_rows(
    rows: list[dict[str, Any]], existing: dict[str, Objective]
) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for row in rows:
        previous = existing.get(row["id"])
        if previous is not None:
            try:
                if Objective.model_validate(row) == previous:
                    canonical.append(row)
                    continue
            except ValueError:
                pass
        technique_ids = row.get("mitre") or []
        annotation = row.get("attack")
        tactic_id = row.get("attack_tactic_id")
        if tactic_id is None and isinstance(annotation, dict):
            tactic_id = annotation.get("tactic_id")
        if tactic_id is None:
            if technique_ids:
                if (
                    previous is not None
                    and previous.attack is None
                    and technique_ids == previous.mitre
                ):
                    canonical.append(
                        {key: value for key, value in row.items() if key != "attack_tactic_id"}
                    )
                    continue
                raise ValueError(
                    f"Objective {row['id']} has ATT&CK techniques but no tactic_id; "
                    "select a tactic from the v19.2 catalog"
                )
            canonical.append(
                {key: value for key, value in row.items() if key != "attack_tactic_id"}
            )
            continue
        resolved = canonical_attack_annotation(tactic_id, technique_ids)
        canonical.append(
            {
                **{key: value for key, value in row.items() if key != "attack_tactic_id"},
                "attack": resolved.model_dump(mode="json"),
            }
        )
    return canonical


def _tool_error(tool_call_id: str, content: str) -> Command[Any]:
    return Command(
        update={
            "messages": [ToolMessage(content=content, tool_call_id=tool_call_id, status="error")]
        }
    )


def _evidence_error(
    backend: BackendProtocol | None, workspace_path: str | None, refs: list[str]
) -> str | None:
    scoped = _scoped_opplan_backend(backend, workspace_path)
    if scoped is None:
        return "No engagement workspace backend is configured"
    for ref in refs:
        path = posixpath.normpath(ref)
        if not ref.startswith("/workspace/") or not path.startswith("/workspace/"):
            return f"Evidence path must be under /workspace: {ref}"
        try:
            result = scoped.read(path, offset=0, limit=1)
        except Exception as exc:
            return f"Cannot read evidence {path}: {exc}"
        if result.error:
            return f"Cannot read evidence {path}: {result.error}"
    return None


def _build_opplan_payload(opplan: OPPLAN) -> dict[str, Any]:
    """Render an OPPLAN as a stable, human-readable JSON document.

    Format ``v2``::

        {
          "schema_version": "2",
          "saved_at": "2026-05-09T09:30:00+00:00",
          "engagement_name": "<slug>",
          "threat_profile": "<text>",
          "revision": 1,
          "facts": [],
          "summary": {
              "total": 5,
              "completed": 2,
              "in_progress": 1,
              "blocked": 1,
              "cancelled": 0,
              "pending": 1
          },
          "objectives": [ {Objective json}, ... ]   # sorted by id
        }

    Objectives are sorted by ``id`` so consecutive saves diff cleanly under
    git, and a top-level ``summary`` block lets a human or ops tool read
    progress without parsing the full list. The persisted schema is a
    *superset* of the runtime ``OPPLAN`` model — the wrapper fields
    (schema_version, saved_at, summary) are dropped silently by
    ``OPPLAN(**data)`` thanks to Pydantic's default extra-field policy.
    """
    objectives_json = [
        obj.model_dump(mode="json") for obj in sorted(opplan.objectives, key=lambda o: o.id)
    ]
    summary: dict[str, int] = {"total": len(objectives_json)}
    for status_value in (
        ObjectiveStatus.PENDING,
        ObjectiveStatus.IN_PROGRESS,
        ObjectiveStatus.COMPLETED,
        ObjectiveStatus.BLOCKED,
        ObjectiveStatus.CANCELLED,
    ):
        key = status_value.value.replace("-", "_")
        summary[key] = sum(1 for o in objectives_json if o.get("status") == status_value.value)
    return {
        "schema_version": OPPLAN_FILE_SCHEMA_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engagement_name": opplan.engagement_name,
        "threat_profile": opplan.threat_profile,
        "revision": opplan.revision,
        "facts": [fact.model_dump(mode="json") for fact in opplan.facts],
        "summary": summary,
        "objectives": objectives_json,
    }


def _live_sandbox_backend(fallback: BackendProtocol | None) -> BackendProtocol | None:
    """Resolve the CURRENT run's sandbox backend, not the graph-build-time one.

    The backend captured when the graph is compiled (``OPPLANMiddleware(backend=)``)
    resolves its sandbox endpoint from the ``SANDBOX_URL`` env var, because there
    is no run config at construction time. In a SHARED multi-tenant langgraph that
    env points at the process-local sidecar, NOT the run's OWN per-engagement
    sandbox (per-run VM / silo). So OPPLAN persistence wrote ``opplan.json`` to the
    sidecar's shared workspace (bucket ROOT, unprefixed) while every OTHER doc —
    written through FilesystemMiddleware, which rebinds per run — landed in the
    engagement's tenant bucket. ``read_file`` then resolved against the tenant
    prefix and could never see the OPPLAN.

    Re-resolve per call from the ambient run config, mirroring
    ``FilesystemMiddleware`` (``build_sandbox_backend`` reads
    ``config.configurable.sandbox_url`` since the per-run-sandbox fix). OPPLAN
    runs only in the TOP-LEVEL orchestrator, where langgraph seeds the
    ``get_config()`` contextvar that ``build_sandbox_backend()`` reads — so no
    explicit config threading is needed here (unlike a sub-agent). Falls back to
    the captured backend when there is no active run (unit tests) or if
    resolution raises, so behaviour is unchanged off the hosted path.
    """
    # Only re-resolve when an ACTIVE run actually seeds a per-run sandbox_url
    # (the multi-tenant orchestrator path this exists for). Off that path — unit
    # tests, build-time construction, single-tenant/env deploys — there is no
    # per-run sandbox to reach, and ``build_sandbox_backend()`` does NOT raise:
    # it returns an HTTPSandbox for the default ``http://localhost:9999``. Writing
    # OPPLAN through that dead endpoint hangs (the persist test hung the whole
    # middleware suite → CI 30-min timeout). The captured ``fallback`` already
    # points at the right backend for those cases, so use it.
    run_backend = _run_sandbox_backend.get()
    if run_backend is not None:
        return run_backend
    try:
        from langgraph.config import get_config

        sandbox_url = ((get_config() or {}).get("configurable") or {}).get("sandbox_url")
    except Exception:
        sandbox_url = None
    if not sandbox_url:
        return fallback
    try:
        from decepticon.backends import build_sandbox_backend, make_agent_backend

        return make_agent_backend(build_sandbox_backend())
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("OPPLAN live backend resolution failed, using build-time backend: %s", exc)
        return fallback


def _scoped_opplan_backend(
    backend: BackendProtocol | None,
    workspace_path: str | None,
) -> BackendProtocol | None:
    """Return a backend scoped to the active engagement workspace."""
    if backend is None:
        return None
    if not workspace_path:
        return None

    # Rebind to the run's OWN sandbox before scoping — the captured ``backend``
    # points at the build-time env sidecar in a shared langgraph (see
    # ``_live_sandbox_backend``), which routed OPPLAN writes to the wrong bucket.
    live = _live_sandbox_backend(backend)
    if live is None:
        return None

    # Local import avoids a module cycle: filesystem.py imports the OPPLAN
    # reducers for its state schema.
    from decepticon.middleware.filesystem import EngagementFilesystemBackend

    return EngagementFilesystemBackend(live, workspace_path)


def _read_text_from_backend(
    backend: BackendProtocol, file_path: str
) -> tuple[str | None, str | None]:
    result = backend.read(file_path, offset=0, limit=1_000_000)
    if result.error:
        return None, result.error
    data = result.file_data
    if not isinstance(data, dict):
        return None, f"File '{file_path}': backend returned no file data"
    if data.get("encoding", "utf-8") != "utf-8":
        return None, f"File '{file_path}': expected utf-8 text, got {data.get('encoding')}"
    content = data.get("content", "")
    if not isinstance(content, str):
        return None, f"File '{file_path}': backend returned non-text content"
    return content, None


def _write_text_to_backend(
    backend: BackendProtocol,
    file_path: str,
    content: str,
    expected_content: str | None | object = _UNSET,
) -> str | None:
    """Create or overwrite a text file through the configured backend."""
    if expected_content is _UNSET:
        old_content, read_error = _read_text_from_backend(backend, file_path)
    elif expected_content is None or isinstance(expected_content, str):
        old_content, read_error = expected_content, None
    else:
        return "Invalid expected OPPLAN content"
    if old_content is None:
        write_result = backend.write(file_path, content)
        if write_result.error:
            return (
                f"read failed: {read_error}; write failed: {write_result.error}"
                if read_error
                else write_result.error
            )
        return None

    if old_content == content:
        return None

    edit_result = backend.edit(file_path, old_content, content)
    return edit_result.error


def _persist_opplan_to_backend(
    backend: BackendProtocol | None,
    workspace_path: str | None,
    objectives: list[dict],
    engagement_name: str,
    threat_profile: str,
    revision: int = 0,
    facts: list[dict] | None = None,
    strict: bool = False,
    expected_revision: int | None = None,
) -> str | None:
    """Write the current OPPLAN to ``/workspace/plan/opplan.json`` via backend.

    Best-effort: a missing backend/workspace_path (e.g. a unit-level tool
    call before agent wiring) skips with a debug log, and any backend /
    serialization error is logged rather than raised. The caller has already
    mutated agent state by the time this runs; raising here would put the LLM
    in a retry loop while state and the backend file diverge. The next
    mutation retries.

    The persisted JSON is wrapped with metadata (schema_version, saved_at,
    summary) for human/ops readability; see :func:`_build_opplan_payload`.
    """
    scoped_backend = _scoped_opplan_backend(backend, workspace_path)
    if scoped_backend is None:
        log.debug("OPPLAN persistence skipped: backend or workspace_path missing")
        return "No engagement workspace backend is configured" if strict else None
    try:
        current_text: str | None | object = _UNSET
        if expected_revision is not None:
            current_text, read_error = _read_text_from_backend(scoped_backend, OPPLAN_VIRTUAL_PATH)
            if current_text is None:
                if expected_revision or not (
                    "file_not_found" in (read_error or "")
                    or "not found" in (read_error or "").lower()
                ):
                    return f"Cannot compare OPPLAN revision: {read_error}"
            else:
                disk_revision = int(json.loads(current_text).get("revision", 0))
                if disk_revision != expected_revision:
                    return (
                        f"OPPLAN changed on disk: expected revision {expected_revision}, "
                        f"found {disk_revision}"
                    )
        opplan = OPPLAN(
            engagement_name=engagement_name or "",
            threat_profile=threat_profile or "",
            objectives=[Objective(**o) for o in objectives],
            revision=revision,
            facts=[PlanFact.model_validate(fact) for fact in (facts or [])],
        )
        error = _write_text_to_backend(
            scoped_backend,
            OPPLAN_VIRTUAL_PATH,
            json.dumps(_build_opplan_payload(opplan), indent=2, ensure_ascii=False),
            current_text,
        )
        if error:
            log.warning("OPPLAN persistence failed for workspace=%s: %s", workspace_path, error)
            return error
    except Exception as e:  # noqa: BLE001 — best-effort persistence
        log.warning("OPPLAN persistence failed for workspace=%s: %s", workspace_path, e)
        return str(e)
    return None


def _format_opplan_for_agent(
    objectives: list[dict],
    engagement_name: str,
    threat_profile: str,
    status_ready_ids: tuple[str, ...] | None = None,
) -> str:
    """Format OPPLAN for list_objectives response (detailed overview).

    When any objective has ``parent_id`` set, the output includes an
    indented tree view after the flat table so the agent can see the
    hierarchy at a glance.
    """
    total = len(objectives)
    completed = sum(1 for o in objectives if o.get("status") == "completed")
    blocked = sum(1 for o in objectives if o.get("status") == "blocked")

    has_tree = any(o.get("parent_id") for o in objectives)

    lines = [
        f"# OPPLAN: {engagement_name}",
        f"Threat Profile: {threat_profile}",
        f"Progress: {completed}/{total} completed, {blocked} blocked",
        "",
        "| ID | Phase | Title | Status | Priority | Owner | Blocked By |",
        "|---|---|---|---|---|---|---|",
    ]

    for o in sorted(objectives, key=lambda x: x.get("priority", 999)):
        status = o.get("status", "pending")
        blocked_by = ", ".join(o.get("blocked_by", [])) or "-"
        title = o.get("title", "?")
        if o.get("parent_id"):
            title = f"↳ {title}"
        lines.append(
            f"| {o.get('id', '?')} | {o.get('phase', '?')} | "
            f"{title} | {status} | "
            f"{o.get('priority', '?')} | {o.get('owner') or '-'} | "
            f"{blocked_by} |"
        )

    lines.append("")

    if has_tree:
        lines.append("## Task Tree")

        # ``visited`` guards against parent_id cycles so a malformed plan
        # cannot hang the agent in unbounded recursion when list_objectives
        # or any prompt-injection path renders the tree.
        visited: set[str] = set()

        def _render(parent_id: str | None, depth: int) -> None:
            kids = sorted(
                [o for o in objectives if o.get("parent_id") == parent_id],
                key=lambda x: x.get("priority", 999),
            )
            for o in kids:
                obj_id = o.get("id")
                if not obj_id or obj_id in visited:
                    continue
                visited.add(obj_id)
                indent = "  " * depth
                status = o.get("status", "pending")
                marker = {
                    "completed": "[x]",
                    "blocked": "[!]",
                    "cancelled": "[-]",
                    "in-progress": "[~]",
                }.get(status, "[ ]")
                lines.append(f"{indent}- {marker} {obj_id} {o.get('title', '?')} ({status})")
                _render(obj_id, depth + 1)

        _render(None, 0)
        lines.append("")

    # Next objective recommendation
    actionable = [
        o
        for o in objectives
        if (
            o.get("id") in status_ready_ids
            if status_ready_ids is not None
            else o.get("status") in ("pending", "in-progress")
        )
    ]
    actionable.sort(key=lambda o: o.get("priority", 999))
    if actionable:
        nxt = actionable[0]
        lines.append(
            f"Next: {nxt.get('id')} — {nxt.get('title')} "
            f"(phase: {nxt.get('phase')}, priority: {nxt.get('priority')})"
        )
    else:
        all_done = bool(objectives) and all(o.get("status") == "completed" for o in objectives)
        if all_done:
            lines.append("ALL OBJECTIVES COMPLETE — Generate final engagement report.")
        else:
            lines.append("No actionable objectives — review blocked items for retry.")

    return "\n".join(lines)


# ── Tool Definitions ──────────────────────────────────────────────────


def build_opplan_tools(backend: BackendProtocol | None = None) -> list:
    """Create OPPLAN tools with InjectedState for direct state access.

    Tool bodies execute CRUD logic directly, returning Command for state
    mutations. No middleware interception is needed; tools appear as proper
    `tool` type runs in LangSmith.
    """

    @tool(
        description=(
            "Commit the complete OPPLAN objective DAG. Omit id for each new node; "
            "the server issues a UUID. Reference a new node by its zero-based position "
            "in objectives from blocked_by, any_of, parent_id, or a fact producer_id. "
            "Existing nodes retain their IDs. expected_revision must match the "
            "current plan. Running and terminal objectives cannot be rewritten or removed. "
            "Use this to create or replan a DAG, then dispatch only ready objectives."
        )
    )
    def commit_opplan(
        objectives: list[dict[str, Any]],
        expected_revision: int,
        state: Annotated[dict, InjectedState],
        facts: list[dict[str, Any]] | None = None,
        engagement_name: str | None = None,
        threat_profile: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        current_revision = int(state.get("plan_revision", 0))
        if expected_revision != current_revision:
            return _tool_error(
                tool_call_id,
                f"Stale OPPLAN revision: expected {current_revision}, got {expected_revision}.",
            )
        try:
            old = {row["id"]: Objective.model_validate(row) for row in state.get("objectives", [])}
            objective_rows, fact_rows, issued = _assign_objective_ids(
                objectives,
                facts if facts is not None else state.get("plan_facts", []),
                set(old),
            )
            objective_rows = _canonicalize_attack_rows(objective_rows, old)
            proposed = [Objective.model_validate(row) for row in objective_rows]
            proposed_facts = [PlanFact.model_validate(row) for row in fact_rows]
            inspection = inspect_plan(proposed, proposed_facts)
        except Exception as exc:
            return _tool_error(tool_call_id, f"Invalid OPPLAN payload: {exc}")
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)
        if not proposed:
            return _tool_error(tool_call_id, "OPPLAN must contain at least one objective.")
        new = {objective.id: objective for objective in proposed}
        for objective_id, objective in old.items():
            if objective_id in new and new[objective_id].status != objective.status:
                return _tool_error(
                    tool_call_id,
                    f"Use update_objective to change status of {objective_id}.",
                )
            if (
                objective.status
                in {
                    ObjectiveStatus.IN_PROGRESS,
                    ObjectiveStatus.COMPLETED,
                    ObjectiveStatus.CANCELLED,
                }
                and new.get(objective_id) != objective
            ):
                return _tool_error(
                    tool_call_id,
                    f"Cannot rewrite or remove active/terminal objective {objective_id}.",
                )
        for objective in proposed:
            if objective.id not in old and objective.status != ObjectiveStatus.PENDING:
                return _tool_error(
                    tool_call_id,
                    f"New objective {objective.id} must start pending.",
                )
        old_facts = {row["id"]: PlanFact.model_validate(row) for row in state.get("plan_facts", [])}
        new_facts = {fact.id: fact for fact in proposed_facts}
        for fact_id, fact in old_facts.items():
            if fact.verified and new_facts.get(fact_id) != fact:
                return _tool_error(tool_call_id, f"Cannot rewrite verified fact {fact_id}.")
        for fact in proposed_facts:
            if fact.verified and (fact.id not in old_facts or not old_facts[fact.id].verified):
                return _tool_error(
                    tool_call_id, f"Use record_plan_fact to record evidence for {fact.id}."
                )
        name = engagement_name or state.get("engagement_name", "")
        profile = threat_profile or state.get("threat_profile", "")
        next_revision = current_revision + 1
        rows = [objective.model_dump(mode="json") for objective in proposed]
        error = _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            rows,
            name,
            profile,
            revision=next_revision,
            facts=[fact.model_dump(mode="json") for fact in proposed_facts],
            strict=True,
            expected_revision=current_revision,
        )
        if error:
            return _tool_error(tool_call_id, f"OPPLAN was not committed: {error}")
        counter = max(
            (
                int(item.id[4:])
                for item in proposed
                if item.id.startswith("OBJ-") and item.id[4:].isdigit()
            ),
            default=0,
        )
        return Command(
            update={
                "objectives": rows,
                "plan_revision": next_revision,
                "plan_facts": [fact.model_dump(mode="json") for fact in proposed_facts],
                "objective_counter": counter,
                "engagement_name": name,
                "threat_profile": profile,
                "messages": [
                    ToolMessage(
                        content=(
                            f"Committed OPPLAN revision {next_revision} with {len(rows)} objectives. "
                            f"Issued IDs by submitted position: {issued}. "
                            f"Status-ready: {', '.join(inspection.status_ready_ids) or 'none'}. "
                            "Recheck RoE and evidence before dispatch."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Attach workspace evidence to a declared plan fact after its producer objective "
            "completes. This checks evidence path existence, not the truth of its contents."
        )
    )
    def record_plan_fact(
        fact_id: str,
        evidence_refs: list[str],
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        if not state.get("plan_revision", 0):
            return _tool_error(tool_call_id, "Create a versioned DAG before recording facts.")
        facts = [PlanFact.model_validate(row) for row in state.get("plan_facts", [])]
        fact = next((item for item in facts if item.id == fact_id), None)
        if fact is None:
            return _tool_error(tool_call_id, f"Fact {fact_id} is not declared in OPPLAN.")
        if fact.verified:
            return _tool_error(tool_call_id, f"Fact {fact_id} already has evidence.")
        producer = next(
            (row for row in state.get("objectives", []) if row.get("id") == fact.producer_id),
            None,
        )
        if producer is None or producer.get("status") != "completed":
            return _tool_error(tool_call_id, f"Producer {fact.producer_id} must complete first.")
        if not evidence_refs:
            return _tool_error(tool_call_id, "A fact requires at least one evidence path.")
        evidence_error = _evidence_error(backend, state.get("workspace_path"), evidence_refs)
        if evidence_error:
            return _tool_error(tool_call_id, evidence_error)
        updated_facts = [
            item.model_copy(update={"evidence_refs": evidence_refs, "verified": True})
            if item.id == fact_id
            else item
            for item in facts
        ]
        inspection = inspect_plan(
            [Objective.model_validate(row) for row in state.get("objectives", [])],
            updated_facts,
        )
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)
        error = _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            state.get("objectives", []),
            state.get("engagement_name", ""),
            state.get("threat_profile", ""),
            revision=int(state["plan_revision"]) + 1,
            facts=[item.model_dump(mode="json") for item in updated_facts],
            strict=True,
            expected_revision=int(state["plan_revision"]),
        )
        if error:
            return _tool_error(tool_call_id, f"Fact was not saved: {error}")
        return Command(
            update={
                "plan_facts": [item.model_dump(mode="json") for item in updated_facts],
                "plan_revision": int(state["plan_revision"]) + 1,
                "messages": [
                    ToolMessage(
                        content=f"Recorded evidence for {fact_id}. The artifact exists; review its contents before relying on its claim.",
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Revoke a verified plan fact when its evidence is invalidated. This blocks active "
            "and completed dependent objectives transitively, while preserving the audit trail."
        )
    )
    def revoke_plan_fact(
        fact_id: str,
        reason: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        revision = int(state.get("plan_revision", 0))
        if not revision:
            return _tool_error(tool_call_id, "Create a versioned DAG before revoking facts.")
        if not reason.strip():
            return _tool_error(tool_call_id, "A fact revocation requires a reason.")
        objectives = [Objective.model_validate(row) for row in state.get("objectives", [])]
        facts = [PlanFact.model_validate(row) for row in state.get("plan_facts", [])]
        inspection = inspect_plan(objectives, facts)
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)
        target = next((fact for fact in facts if fact.id == fact_id), None)
        if target is None or not target.verified:
            return _tool_error(tool_call_id, f"Fact {fact_id} is not verified in OPPLAN.")

        facts = [
            fact.model_copy(update={"verified": False}) if fact.id == fact_id else fact
            for fact in facts
        ]
        affected: set[str] = set()
        while True:
            changed = False
            by_id = {objective.id: objective for objective in objectives}
            for index, fact in enumerate(facts):
                if fact.verified and by_id[fact.producer_id].status != ObjectiveStatus.COMPLETED:
                    facts[index] = fact.model_copy(update={"verified": False})
                    changed = True
            facts_by_id = {fact.id: fact for fact in facts}
            for index, objective in enumerate(objectives):
                if objective.status not in {ObjectiveStatus.IN_PROGRESS, ObjectiveStatus.COMPLETED}:
                    continue
                unmet = unmet_prerequisites(objective, by_id, facts_by_id)
                incomplete_children = any(
                    child.parent_id == objective.id
                    and child.status not in {ObjectiveStatus.COMPLETED, ObjectiveStatus.CANCELLED}
                    for child in objectives
                )
                if not unmet and not incomplete_children:
                    continue
                objectives[index] = objective.model_copy(
                    update={
                        "status": ObjectiveStatus.BLOCKED,
                        "outcome": ObjectiveOutcome.INVALIDATED,
                        "owner": "",
                        "notes": "\n".join(
                            part
                            for part in (
                                objective.notes,
                                f"Invalidated by {fact_id}: {reason.strip()}",
                            )
                            if part
                        ),
                    }
                )
                affected.add(objective.id)
                changed = True
            if not changed:
                break

        final_inspection = inspect_plan(objectives, facts)
        if final_inspection.issues:
            return _graph_rejection(final_inspection, tool_call_id)
        rows = [objective.model_dump(mode="json") for objective in objectives]
        fact_rows = [fact.model_dump(mode="json") for fact in facts]
        error = _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            rows,
            state.get("engagement_name", ""),
            state.get("threat_profile", ""),
            revision=revision + 1,
            facts=fact_rows,
            strict=True,
            expected_revision=revision,
        )
        if error:
            return _tool_error(tool_call_id, f"Fact revocation was not saved: {error}")
        return Command(
            update={
                "objectives": rows,
                "plan_facts": fact_rows,
                "plan_revision": revision + 1,
                "messages": [
                    ToolMessage(
                        content=(
                            f"Revoked {fact_id}. Blocked dependent objectives: "
                            f"{', '.join(sorted(affected)) or 'none'}. Reassess the evidence before retry."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Add a single objective to the OPPLAN. The server issues a UUID. "
            "Each objective must be completable in "
            "ONE sub-agent context window. Use blocked_by to set kill chain dependencies. "
            "Set engagement_name and threat_profile on the first call to initialize context. "
            "Auto-persists the OPPLAN through the engagement filesystem backend "
            "to /workspace/plan/opplan.json on success. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def add_objective(
        title: str,
        phase: ObjectivePhase,
        description: str,
        acceptance_criteria: list[str],
        priority: int,
        state: Annotated[dict, InjectedState],
        engagement_name: str | None = None,
        threat_profile: str | None = None,
        mitre: list[str] | None = None,
        attack_tactic_id: str | None = None,
        opsec: OpsecLevel = OpsecLevel.STANDARD,
        opsec_notes: str = "",
        c2_tier: C2Tier = C2Tier.INTERACTIVE,
        concessions: list[str] | None = None,
        blocked_by: list[str] | None = None,
        parent_id: str | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Add one objective with auto-ID generation."""
        if state.get("plan_revision", 0):
            return _tool_error(tool_call_id, "Use commit_opplan to revise a versioned DAG.")
        counter = state.get("objective_counter", 0)
        obj_id = str(uuid4())

        # Validate parent_id if supplied
        if parent_id:
            existing_ids = {o.get("id") for o in state.get("objectives", [])}
            if parent_id not in existing_ids:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    f"Parent objective '{parent_id}' not found. "
                                    f"Existing: {', '.join(sorted(i for i in existing_ids if i))}"
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )
            parent = next(
                objective
                for objective in state.get("objectives", [])
                if objective.get("id") == parent_id
            )
            if parent.get("status") in {"completed", "cancelled"}:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    f"Cannot add a child to completed or cancelled "
                                    f"parent {parent_id}."
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ]
                    }
                )

        obj_dict = {
            "id": obj_id,
            "title": title,
            "phase": phase,
            "description": description,
            "acceptance_criteria": acceptance_criteria,
            "priority": priority,
            "status": "pending",
            "mitre": mitre or [],
            "opsec": opsec,
            "opsec_notes": opsec_notes,
            "c2_tier": c2_tier,
            "concessions": concessions or [],
            "blocked_by": blocked_by or [],
            "owner": "",
            "notes": "",
            "parent_id": parent_id,
        }

        try:
            obj_dict = _canonicalize_attack_rows(
                [obj_dict | {"attack_tactic_id": attack_tactic_id}], {}
            )[0]
        except ValueError as exc:
            return _tool_error(tool_call_id, str(exc))

        # Pydantic validation
        try:
            Objective(**obj_dict)
        except Exception as e:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Validation failed for objective: {e}",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )

        objectives = list(state.get("objectives", []))
        objectives.append(obj_dict)
        inspection = _inspect_objective_rows(objectives)
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)

        # Ground-truth telemetry: which kill-chain phase the engagement is
        # working — no objective text/target. No-op unless telemetry is on.
        try:
            from decepticon.telemetry.sink import get_sink, session_id_for

            sid = session_id_for(engagement_name or state.get("engagement_name", ""))
            get_sink().record_phase(getattr(phase, "value", str(phase)), "pending", session_id=sid)
        except Exception:  # noqa: BLE001 — telemetry must never break the tool
            pass

        # Build state update — always include objectives + counter
        update: dict[str, Any] = {
            "objectives": objectives,
            "objective_counter": counter,
            "messages": [
                ToolMessage(
                    content=(
                        f"Added {obj_id}: {obj_dict['title']} "
                        f"(phase: {obj_dict['phase']}, priority: {obj_dict['priority']})"
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }

        # Set engagement metadata if provided (typically on first call)
        if engagement_name:
            update["engagement_name"] = engagement_name
        if threat_profile:
            update["threat_profile"] = threat_profile

        _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            objectives,
            engagement_name or state.get("engagement_name", ""),
            threat_profile or state.get("threat_profile", ""),
        )

        return Command(update=update)

    @tool(
        description=(
            "Read a single objective's full details by ID. "
            "ALWAYS call this before update_objective to prevent staleness. "
            "Returns: status, description, acceptance criteria, dependencies, notes. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def get_objective(
        objective_id: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Read one objective detail from state."""
        objectives = state.get("objectives", [])
        target = next((o for o in objectives if o.get("id") == objective_id), None)

        if not target:
            available = ", ".join(o.get("id", "?") for o in objectives)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=(
                                f"Objective '{objective_id}' not found. "
                                f"Available: {available or 'none (use add_objective first)'}"
                            ),
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )

        obj_status = target.get("status", "pending")
        mitre_ids = target.get("mitre") or []
        mitre_str = ", ".join(mitre_ids) if mitre_ids else "n/a"
        lines = [
            f"## {target['id']} [{obj_status.upper()}]",
            f"Title: {target.get('title', '')}",
            f"Phase: {target.get('phase', '')} | Priority: {target.get('priority', '')}",
            f"MITRE: {mitre_str}",
            f"OPSEC: {target.get('opsec', 'standard')} | C2: {target.get('c2_tier', 'interactive')}",
            f"Description: {target.get('description', '')}",
        ]

        criteria = target.get("acceptance_criteria", [])
        if criteria:
            check = "x" if obj_status == "completed" else " "
            lines.append("Acceptance Criteria:")
            for c in criteria:
                lines.append(f"  - [{check}] {c}")

        blocked_by_ids = target.get("blocked_by", [])
        if blocked_by_ids:
            lines.append(f"Blocked By: {', '.join(blocked_by_ids)}")

        owner = target.get("owner", "")
        if owner:
            lines.append(f"Owner: {owner}")

        obj_opsec_notes = target.get("opsec_notes", "")
        if obj_opsec_notes:
            lines.append(f"OPSEC Notes: {obj_opsec_notes}")

        obj_concessions = target.get("concessions") or []
        if obj_concessions:
            lines.append("Concessions:")
            for c in obj_concessions:
                lines.append(f"  - {c}")

        notes = target.get("notes", "")
        if notes:
            lines.append(f"Notes: {notes}")

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content="\n".join(lines),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "List all OPPLAN objectives with progress summary. "
            "Returns: engagement overview, objective table with status, "
            "and next recommended objective. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def list_objectives(
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """List all objectives with progress summary."""
        objectives = state.get("objectives", [])
        engagement = state.get("engagement_name", "")
        threat = state.get("threat_profile", "")

        if not objectives:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="No objectives defined yet. Use `commit_opplan` to create a versioned DAG.",
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        inspection = _inspect_objective_rows(objectives, state.get("plan_facts", []))
        content = _format_opplan_for_agent(
            objectives, engagement, threat, inspection.status_ready_ids
        )
        if inspection.issues:
            details = "; ".join(issue.describe() for issue in inspection.issues)
            content += f"\nGraph integrity warnings: {details}"
        else:
            ready = ", ".join(inspection.status_ready_ids) or "none"
            content += f"\nStatus-ready candidates: {ready} (RoE and evidence not checked)"
        return Command(
            update={
                "messages": [ToolMessage(content=content, tool_call_id=tool_call_id)],
            }
        )

    @tool(
        description=(
            "Update a single objective. MUST call get_objective first. "
            "Can change: status, notes, owner, add_blocked_by. "
            "Valid transitions: pending→in-progress, in-progress→completed/blocked, "
            "blocked→in-progress (retry) or completed (abandon). "
            "Include evidence when marking completed, failure reason when marking blocked. "
            "Auto-persists the OPPLAN through the engagement filesystem backend "
            "to /workspace/plan/opplan.json on success. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def update_objective(
        objective_id: str,
        state: Annotated[dict, InjectedState],
        status: str | None = None,
        notes: str | None = None,
        owner: str | None = None,
        add_blocked_by: list[str] | None = None,
        outcome: str | None = None,
        evidence_refs: list[str] | None = None,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Update one objective with state transition validation."""
        # Deep copy objectives to avoid mutating state
        objectives = [dict(o) for o in state.get("objectives", [])]
        target = next((o for o in objectives if o.get("id") == objective_id), None)

        if not target:
            available = ", ".join(o.get("id", "?") for o in objectives)
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Objective '{objective_id}' not found. Available: {available}",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )

        versioned = bool(state.get("plan_revision", 0))
        if versioned and add_blocked_by:
            return _tool_error(tool_call_id, "Use commit_opplan to revise DAG dependencies.")
        if versioned and status == "blocked":
            if (outcome or target.get("outcome")) not in {
                "inconclusive",
                "infrastructure-error",
                "scope-refused",
                "invalidated",
            }:
                return _tool_error(
                    tool_call_id,
                    "Blocked objectives require inconclusive, infrastructure-error, scope-refused, or invalidated outcome.",
                )
            if not (notes or target.get("notes")):
                return _tool_error(tool_call_id, "Blocked objectives require a reason in notes.")
        if versioned and status in {"in-progress", "completed"}:
            by_id = {row["id"]: row for row in objectives}
            facts = {row["id"]: row for row in state.get("plan_facts", [])}
            unmet = [
                dependency
                for dependency in target.get("blocked_by", [])
                if by_id.get(dependency, {}).get("status") != "completed"
            ]
            unmet.extend(
                "one of " + ", ".join(group)
                for group in target.get("any_of", [])
                if not any(by_id.get(item, {}).get("status") == "completed" for item in group)
            )
            unmet.extend(
                "fact " + fact_id
                for fact_id in target.get("required_fact_ids", [])
                if not (
                    facts.get(fact_id, {}).get("verified")
                    and facts.get(fact_id, {}).get("evidence_refs")
                    and by_id.get(facts.get(fact_id, {}).get("producer_id"), {}).get("status")
                    == "completed"
                )
            )
            if unmet:
                return _tool_error(
                    tool_call_id,
                    f"Objective {objective_id} prerequisites are unmet: {', '.join(unmet)}.",
                )
        if versioned and status == "completed":
            if target.get("status") == "blocked":
                return _tool_error(tool_call_id, "Retry the blocked objective before completion.")
            if not (outcome or target.get("outcome")):
                return _tool_error(
                    tool_call_id, "Completed objectives require an explicit outcome."
                )
            if not (evidence_refs or target.get("evidence_refs")):
                return _tool_error(
                    tool_call_id, "Completed objectives require evidence references."
                )
            if (outcome or target.get("outcome")) not in {"finding", "no-finding", "objective-met"}:
                return _tool_error(
                    tool_call_id,
                    "Completion outcome must be finding, no-finding, or objective-met.",
                )
            evidence_error = _evidence_error(
                backend,
                state.get("workspace_path"),
                evidence_refs or target.get("evidence_refs", []),
            )
            if evidence_error:
                return _tool_error(tool_call_id, evidence_error)

        updated_fields: list[str] = []

        # ── Status change with transition + dependency validation ─────
        if status is not None:
            # Validate status value
            try:
                ObjectiveStatus(status)
            except ValueError:
                valid = ", ".join(s.value for s in ObjectiveStatus)
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=f"Invalid status '{status}'. Valid: {valid}",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )

            current = target.get("status", "pending")
            if not _is_valid_transition(current, status):
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    f"Invalid transition: {current} → {status}. "
                                    f"Valid from '{current}': {_valid_next(current)}"
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )

            # Parents cannot complete until every child is done.
            if status == "completed":
                children = [o for o in objectives if o.get("parent_id") == objective_id]
                if children:
                    unresolved_kids = [
                        c.get("id", "<?>")
                        for c in children
                        if c.get("status") not in {"completed", "cancelled"}
                    ]
                    if unresolved_kids:
                        return Command(
                            update={
                                "messages": [
                                    ToolMessage(
                                        content=(
                                            f"Cannot complete {objective_id}: "
                                            f"children still open: {', '.join(unresolved_kids)}. "
                                            f"Complete or cancel each child first, or call "
                                            f"objective_collapse({objective_id})."
                                        ),
                                        tool_call_id=tool_call_id,
                                        status="error",
                                    )
                                ],
                            }
                        )

            target["status"] = status
            updated_fields.append(f"status → {status}")

        # ── Notes ─────────────────────────────────────────────────────
        if notes is not None:
            target["notes"] = notes
            updated_fields.append("notes")
        if outcome is not None:
            target["outcome"] = outcome
            updated_fields.append("outcome")
        if evidence_refs is not None:
            target["evidence_refs"] = evidence_refs
            updated_fields.append("evidence_refs")

        # ── Owner (which sub-agent is executing) ─────────────────────
        if owner is not None:
            target["owner"] = owner
            updated_fields.append("owner")

        # ── Add blocked_by dependencies ──────────────────────────────
        if add_blocked_by:
            existing_blocked = set(target.get("blocked_by", []))
            all_ids = {o.get("id") for o in objectives}
            invalid = [bid for bid in add_blocked_by if bid not in all_ids]
            if invalid:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=f"Invalid blocked_by references: {', '.join(invalid)}",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )
            for bid in add_blocked_by:
                existing_blocked.add(bid)
            target["blocked_by"] = sorted(existing_blocked)
            updated_fields.append("blocked_by")

        if target.get("status") in {"in-progress", "completed"}:
            unresolved = [
                predecessor
                for predecessor in target.get("blocked_by", [])
                if next(
                    objective for objective in objectives if objective["id"] == predecessor
                ).get("status")
                != "completed"
            ]
            if unresolved:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    f"Cannot set {objective_id} to {target['status']}: blocked by unresolved "
                                    f"objectives: {', '.join(unresolved)}"
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ]
                    }
                )

        inspection = _inspect_objective_rows(objectives, state.get("plan_facts", []))
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)

        if not updated_fields:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"No changes specified for {objective_id}.",
                            tool_call_id=tool_call_id,
                        )
                    ],
                }
            )

        if status is not None:
            try:
                from decepticon.telemetry.sink import get_sink, session_id_for

                get_sink().record_phase(
                    str(target.get("phase", "")),
                    status,
                    session_id=session_id_for(state.get("engagement_name", "")),
                )
            except Exception:  # noqa: BLE001 — telemetry must never break the tool
                pass

        total = len(objectives)
        completed_count = sum(1 for o in objectives if o.get("status") == "completed")

        persistence_error = _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            objectives,
            state.get("engagement_name", ""),
            state.get("threat_profile", ""),
            revision=int(state.get("plan_revision", 0)) + (1 if versioned else 0),
            facts=state.get("plan_facts", []),
            strict=versioned,
            expected_revision=int(state["plan_revision"]) if versioned else None,
        )
        if versioned and persistence_error:
            return _tool_error(tool_call_id, f"OPPLAN update was not saved: {persistence_error}")

        return Command(
            update={
                "objectives": objectives,
                **({"plan_revision": int(state["plan_revision"]) + 1} if versioned else {}),
                "messages": [
                    ToolMessage(
                        content=(
                            f"Updated {objective_id}: {', '.join(updated_fields)}. "
                            f"Progress: {completed_count}/{total} completed."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Expand a parent objective into one or more child sub-tasks. "
            "Each child inherits the parent's phase by default but can override it. "
            "Children receive server-issued UUIDs and are added with status 'pending'. "
            "The parent cannot move to COMPLETED until every child is COMPLETED or CANCELLED. "
            "Use this when an objective is broad or when recon reveals sub-tasks — it is "
            "the Pentesting Task Tree (PTT) pattern. Keep children small enough to complete "
            "in one sub-agent iteration. "
            "Auto-persists the OPPLAN through the engagement filesystem backend "
            "to /workspace/plan/opplan.json on success. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def objective_expand(
        parent_id: str,
        children: list[dict],
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Create ``len(children)`` child objectives under ``parent_id``.

        Each child dict must have: ``title`` (str), ``description`` (str),
        ``acceptance_criteria`` (list[str]). Optional: ``phase``
        (ObjectivePhase value, default inherited from parent),
        ``priority`` (int, default parent.priority + N), ``mitre``,
        ``blocked_by``.
        """
        if state.get("plan_revision", 0):
            return _tool_error(tool_call_id, "Use commit_opplan to revise a versioned DAG.")
        objectives = [dict(o) for o in state.get("objectives", [])]
        parent = next((o for o in objectives if o.get("id") == parent_id), None)
        if parent is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Parent objective '{parent_id}' not found.",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )
        if parent.get("status") in {"completed", "cancelled"}:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=(
                                f"Cannot expand {parent_id}: status is "
                                f"{parent.get('status')}. Expand open parents only."
                            ),
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )
        if not children:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="children list is empty — nothing to expand.",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )

        counter = state.get("objective_counter", 0)
        created_ids: list[str] = []
        parent_phase = parent.get("phase")
        try:
            parent_priority = int(parent.get("priority", 100))
        except (ValueError, TypeError):
            parent_priority = 100
        for idx, child in enumerate(children, start=1):
            obj_id = str(uuid4())
            title = str(child.get("title", "")).strip()
            description = str(child.get("description", "")).strip()
            acceptance = child.get("acceptance_criteria") or []
            if not title or not description or not acceptance:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=(
                                    f"Child #{idx} missing required fields "
                                    "(title, description, acceptance_criteria)."
                                ),
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )
            phase = child.get("phase", parent_phase)
            try:
                priority = int(child.get("priority", parent_priority + idx))
            except (ValueError, TypeError):
                priority = parent_priority + idx
            child_dict = {
                "id": obj_id,
                "title": title,
                "phase": phase,
                "description": description,
                "acceptance_criteria": list(acceptance),
                "priority": priority,
                "status": "pending",
                "mitre": list(child.get("mitre") or []),
                "opsec": parent.get("opsec", "standard"),
                "opsec_notes": "",
                "c2_tier": parent.get("c2_tier", "interactive"),
                "concessions": [],
                "blocked_by": list(child.get("blocked_by") or []),
                "owner": "",
                "notes": "",
                "parent_id": parent_id,
            }
            tactic_id = child.get("attack_tactic_id") or (parent.get("attack") or {}).get(
                "tactic_id"
            )
            try:
                child_dict = _canonicalize_attack_rows(
                    [child_dict | {"attack_tactic_id": tactic_id}], {}
                )[0]
            except ValueError as exc:
                return _tool_error(tool_call_id, str(exc))
            try:
                Objective(**child_dict)
            except Exception as e:
                return Command(
                    update={
                        "messages": [
                            ToolMessage(
                                content=f"Child #{idx} validation failed: {e}",
                                tool_call_id=tool_call_id,
                                status="error",
                            )
                        ],
                    }
                )
            objectives.append(child_dict)
            created_ids.append(obj_id)

        inspection = _inspect_objective_rows(objectives)
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)

        _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            objectives,
            state.get("engagement_name", ""),
            state.get("threat_profile", ""),
        )

        return Command(
            update={
                "objectives": objectives,
                "objective_counter": counter,
                "messages": [
                    ToolMessage(
                        content=(
                            f"Expanded {parent_id} into {len(created_ids)} children: "
                            f"{', '.join(created_ids)}"
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Cancel every descendant of a parent objective. Use when abandoning a "
            "hierarchical task — sets each child's status to 'cancelled' so the "
            "parent can then be moved to COMPLETED or CANCELLED itself. "
            "Only pending / in-progress / blocked children are touched; already-done "
            "children are left as-is. "
            "Auto-persists the OPPLAN through the engagement filesystem backend "
            "to /workspace/plan/opplan.json on success. "
            "Call OPPLAN tools sequentially — never in parallel with other OPPLAN tools."
        )
    )
    def objective_collapse(
        parent_id: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Mark every descendant of ``parent_id`` as cancelled."""
        if state.get("plan_revision", 0):
            return _tool_error(tool_call_id, "Use commit_opplan to revise a versioned DAG.")
        objectives = [dict(o) for o in state.get("objectives", [])]
        if not any(o.get("id") == parent_id for o in objectives):
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Parent objective '{parent_id}' not found.",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ],
                }
            )

        # Walk descendants depth-first. ``visited`` guards against cycles in
        # parent_id references (which the schema does not formally rule out)
        # so a malformed plan does not hang the agent in an infinite loop.
        stack = [parent_id]
        visited: set[str] = {parent_id}
        descendants: list[dict[str, Any]] = []
        while stack:
            current = stack.pop()
            for o in objectives:
                if o.get("parent_id") == current:
                    obj_id = o.get("id")
                    if not obj_id or obj_id in visited:
                        continue
                    visited.add(obj_id)
                    descendants.append(o)
                    stack.append(obj_id)

        cancelled: list[str] = []
        for o in descendants:
            if o.get("status") in {"pending", "in-progress", "blocked"}:
                o["status"] = "cancelled"
                cancelled.append(o["id"])

        _persist_opplan_to_backend(
            backend,
            state.get("workspace_path"),
            objectives,
            state.get("engagement_name", ""),
            state.get("threat_profile", ""),
        )

        return Command(
            update={
                "objectives": objectives,
                "messages": [
                    ToolMessage(
                        content=(
                            f"Cancelled {len(cancelled)} descendants of {parent_id}"
                            + (f": {', '.join(cancelled)}" if cancelled else "")
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    @tool(
        description=(
            "Bind the active engagement workspace and load plan/opplan.json when it exists. "
            "Call before filesystem bootstrap: when the file is missing, the workspace path "
            "is still stored in agent state so planning files can be created. "
            "When the file exists, objectives, engagement_name, and threat_profile are "
            "hydrated. Call OPPLAN tools sequentially — never in parallel."
        )
    )
    def load_opplan(
        workspace_path: str,
        state: Annotated[dict, InjectedState],
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> Command[Any]:
        """Read plan/opplan.json and hydrate agent state."""
        scoped_backend = _scoped_opplan_backend(backend, workspace_path)
        if scoped_backend is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=(
                                "No engagement workspace backend is configured. "
                                "Use add_objective after the launcher provides workspace_path."
                            ),
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ]
                }
            )

        raw, read_error = _read_text_from_backend(scoped_backend, OPPLAN_VIRTUAL_PATH)
        if raw is None:
            not_found = (
                "file_not_found" in (read_error or "") or "not found" in (read_error or "").lower()
            )
            content = (
                f"No opplan.json found at {OPPLAN_VIRTUAL_PATH}. "
                "Use add_objective to create a new OPPLAN."
                if not_found
                else f"Failed to load opplan.json: {read_error}"
            )
            update: dict[str, Any] = {
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                ]
            }
            if not_found:
                update["workspace_path"] = workspace_path
            return Command(update=update)

        try:
            data = json.loads(raw)
            opplan = OPPLAN(**data)
        except Exception as e:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Failed to load opplan.json: {e}",
                            tool_call_id=tool_call_id,
                            status="error",
                        )
                    ]
                }
            )

        objectives_raw = [o.model_dump() for o in opplan.objectives]
        inspection = inspect_plan(opplan.objectives, opplan.facts)
        if inspection.issues:
            return _graph_rejection(inspection, tool_call_id)

        # Derive counter from highest existing ID so new objectives don't collide
        counter = 0
        for o in opplan.objectives:
            try:
                n = int(o.id.replace("OBJ-", ""))
                if n > counter:
                    counter = n
            except (ValueError, AttributeError):
                pass

        return Command(
            update={
                "objectives": objectives_raw,
                "engagement_name": opplan.engagement_name,
                "threat_profile": opplan.threat_profile,
                "objective_counter": counter,
                "plan_revision": opplan.revision,
                "plan_facts": [fact.model_dump(mode="json") for fact in opplan.facts],
                "workspace_path": workspace_path,
                "messages": [
                    ToolMessage(
                        content=(
                            f"Loaded {len(objectives_raw)} objectives from {OPPLAN_VIRTUAL_PATH}. "
                            f"Engagement: {opplan.engagement_name} | "
                            f"Revision {opplan.revision}"
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    return [
        commit_opplan,
        record_plan_fact,
        revoke_plan_fact,
        add_objective,
        get_objective,
        list_objectives,
        update_objective,
        objective_expand,
        objective_collapse,
        load_opplan,
    ]


# ── State transition helpers ──────────────────────────────────────────


def _is_valid_transition(current: str, new: str) -> bool:
    """Check if a status transition is allowed."""
    return new in _VALID_TRANSITIONS.get(current, set())


def _valid_next(current: str) -> str:
    """Return comma-separated valid next statuses."""
    return ", ".join(sorted(_VALID_TRANSITIONS.get(current, set())))


__all__ = [
    "OPPLAN_FILE_SCHEMA_VERSION",
    "OPPLAN_TOOL_NAMES",
    "OPPLAN_VIRTUAL_PATH",
    "build_opplan_tools",
    "_build_opplan_payload",
    "_format_opplan_for_agent",
    "_persist_opplan_to_backend",
]
