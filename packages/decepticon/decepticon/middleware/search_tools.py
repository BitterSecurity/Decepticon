from typing import assert_never
from uuid import UUID

from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from decepticon.artifacts import ArtifactError, ArtifactKind, get_artifact
from decepticon.middleware.artifact_tools import registry_from_state
from decepticon.middleware.search_evidence import (
    ProofContext,
    qualifying_findings,
    scoped_backend,
)
from decepticon.middleware.search_policy import (
    Disposition,
    SearchContractError,
    SearchObjective,
    SearchReceipt,
    SearchSession,
    current_events,
    ready_objectives,
    review_plan,
)
from decepticon_core.types.engagement import ACTIONABLE_OBJECTIVE_STATUSES

_OBJECTIVES = TypeAdapter(tuple[SearchObjective, ...])


class ReviewSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    plan_revision: int
    rationale: str
    next_objective_id: str | None = None


class FinalizeSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    disposition: Disposition
    report_document_uuid: UUID
    plan_revision: int
    reason: str


def search_session(runtime: ToolRuntime) -> SearchSession:
    return SearchSession.model_validate_json(runtime.state["search_state"])


def search_error(
    error: SearchContractError | ArtifactError | ValidationError, runtime: ToolRuntime
) -> Command[str]:
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=str(error), status="error", tool_call_id=runtime.tool_call_id or ""
                )
            ]
        }
    )


def build_search_tools(backend: BackendProtocol) -> list[BaseTool]:
    @tool(
        description="After each specialist return, review recon, committed HYP/AP/PATH UUID documents, negative results and capabilities. Commit a revised OPPLAN first when new work is needed, then acknowledge this review using its current revision and next executable objective. Explain the new discriminating experiment or why exploration is exhausted."
    )
    def review_search(request: ReviewSearchRequest, runtime: ToolRuntime) -> Command[str]:
        plan_revision, rationale, next_objective_id = (
            request.plan_revision,
            request.rationale,
            request.next_objective_id,
        )
        try:
            session = search_session(runtime)
            if plan_revision != runtime.state.get("plan_revision", 0):
                raise SearchContractError("Review requires the current OPPLAN revision")
            if not rationale.strip():
                raise SearchContractError("Record evidence-based replanning rationale")
            objectives = _OBJECTIVES.validate_python(runtime.state.get("objectives", []))
            ready = ready_objectives(objectives, tuple(runtime.state.get("plan_facts", [])))
            if next_objective_id is not None and next_objective_id not in ready:
                raise SearchContractError(
                    "Next objective must be a current status-ready OPPLAN leaf"
                )
            if ready and next_objective_id is None:
                raise SearchContractError("Select the next executable objective while work remains")
            session = review_plan(
                session, runtime.state.get("search_task_events", {}), plan_revision
            )
        except (SearchContractError, ArtifactError, ValidationError) as error:
            return search_error(error, runtime)
        return Command(
            update={
                "search_state": session.model_dump_json(),
                "search_last_review": rationale,
                "messages": [
                    ToolMessage(
                        content=f"Search reviewed at OPPLAN revision {plan_revision}. Next: {next_objective_id or 'evaluate explicit stop'}",
                        tool_call_id=runtime.tool_call_id or "",
                    )
                ],
            }
        )

    @tool(
        description="Finalize a search with an explicit disposition and committed assessment-report UUID. Requires reconciled OPPLAN, reviewed specialist returns, matching revision and preserved report snapshot. goal_met requires independently verified High/Critical; negatives or report existence never satisfy it."
    )
    def finalize_search(request: FinalizeSearchRequest, runtime: ToolRuntime) -> Command[str]:
        disposition, report_document_uuid, plan_revision, reason = (
            request.disposition,
            request.report_document_uuid,
            request.plan_revision,
            request.reason,
        )
        try:
            session = search_session(runtime)
            if (
                plan_revision != runtime.state.get("plan_revision", 0)
                or plan_revision != session.reviewed_plan_revision
            ):
                raise SearchContractError("Review current OPPLAN before finalization")
            events = current_events(session, runtime.state.get("search_task_events", {}))
            if set(events) - set(session.reviewed_task_ids):
                raise SearchContractError("New specialist results require search review")
            objectives = _OBJECTIVES.validate_python(runtime.state.get("objectives", []))
            if any(row.status in ACTIONABLE_OBJECTIVE_STATUSES for row in objectives):
                raise SearchContractError("Reconcile all pending/active objectives before closing")
            if not reason.strip():
                raise SearchContractError("An evidence-based stopping reason is required")
            registry = registry_from_state(runtime.state.get("artifact_registry"))
            if not registry.materialized:
                raise SearchContractError("Publish pending document writes before finalizing")
            report = get_artifact(registry, report_document_uuid)
            if report.draft.kind != ArtifactKind.ASSESSMENT_REPORT or not report.revisions:
                raise SearchContractError("Commit the single assessment report before finalizing")
            if report.draft != report.revisions[-1].draft:
                raise SearchContractError("Report has uncommitted draft edits")
            for link in report.revisions[-1].draft.links:
                if link.revision is None or link.content_hash is None:
                    raise SearchContractError(
                        "Final report references must pin UUID, revision and hash"
                    )
            goals = qualifying_findings(
                registry,
                scoped_backend(backend, runtime.state.get("workspace_path")),
                ProofContext(
                    scope_digest=get_config().get("configurable", {}).get("research_scope_digest"),
                    run_id=session.run_id,
                ),
            )
            reported = {
                str(link.document_uuid)
                for link in report.revisions[-1].draft.links
                if str(link.document_uuid) in registry.artifacts
                and link.revision == get_artifact(registry, link.document_uuid).revision
            }
            match disposition:
                case "goal_met":
                    if (
                        not objectives
                        or not goals
                        or not set(goals) <= reported
                        or any(row.status == "blocked" for row in objectives)
                    ):
                        raise SearchContractError(
                            "Goal requires verified High/Critical and completed mandatory coverage; unresolved work is not success"
                        )
                case "budget_exhausted":
                    if session.max_dispatches is None or len(events) < session.max_dispatches:
                        raise SearchContractError("Runtime dispatch budget has not been exhausted")
                case "search_exhausted":
                    hypotheses = [
                        item
                        for item in registry.artifacts.values()
                        if item.draft.kind == ArtifactKind.HYPOTHESIS
                    ]
                    candidates = [
                        item
                        for item in registry.artifacts.values()
                        if item.draft.kind in {ArtifactKind.HYPOTHESIS, ArtifactKind.ATTACK_PLAN}
                    ]
                    if (
                        not events
                        or not hypotheses
                        or any(
                            not item.revisions
                            or item.draft != item.revisions[-1].draft
                            or item.revisions[-1].draft.metadata.get("status")
                            not in {"refuted", "blocked", "independently_verified"}
                            for item in candidates
                        )
                    ):
                        raise SearchContractError(
                            "Resolve registered hypotheses and document alternative-path review before declaring search exhausted"
                        )
                case "blocked":
                    if not any(row.status == "blocked" for row in objectives):
                        raise SearchContractError(
                            "A blocked exit requires an actual blocked OPPLAN objective"
                        )
                case "operator_stopped":
                    raise SearchContractError(
                        "Operator stop is a runtime control, not an agent-declared disposition"
                    )
                case unreachable:
                    assert_never(unreachable)
            version = report.revisions[-1]
            receipt = SearchReceipt(
                thread_id=session.thread_id,
                run_id=session.run_id,
                plan_revision=plan_revision,
                disposition=disposition,
                reason=reason,
                goal_finding_uuids=goals,
                report_document_uuid=str(report.document_uuid),
                report_revision=version.revision,
                report_sha256=version.markdown_sha256,
            )
            session = session.model_copy(
                update={
                    "disposition": disposition,
                    "reason": reason,
                    "status": "terminal",
                    "plan_revision": plan_revision,
                }
            )
        except (SearchContractError, ArtifactError, ValidationError) as error:
            return search_error(error, runtime)
        return Command(
            update={
                "search_state": session.model_dump_json(),
                "search_receipt": receipt.model_dump_json(),
                "messages": [
                    ToolMessage(
                        content=receipt.model_dump_json(), tool_call_id=runtime.tool_call_id or ""
                    )
                ],
            }
        )

    return [review_search, finalize_search]
