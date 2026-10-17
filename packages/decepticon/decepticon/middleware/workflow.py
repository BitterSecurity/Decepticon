"""Pin server-selected workflows and inject their role-scoped contracts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal, NotRequired

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse, OmitFromInput
from langchain_core.messages import SystemMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, JsonValue

from decepticon.workflows import WorkflowCatalog, WorkflowError, WorkflowSnapshot
from decepticon.workflows.legacy import LegacyPlan, legacy_snapshot


def reduce_workflow_snapshot(left: str | None, right: str | None) -> str | None:
    """Parallel specialists may return the same pin, never change it."""
    if left and right and left != right:
        raise WorkflowError("workflow checkpoint pin cannot be changed")
    return left or right


class WorkflowState(AgentState):
    """JSON pin inherited by Deep Agents children and persisted by checkpoints."""

    workflow_snapshot: NotRequired[Annotated[str, reduce_workflow_snapshot, OmitFromInput]]
    objectives: NotRequired[Annotated[list[dict[str, JsonValue]], OmitFromInput]]


class WorkflowSelection(BaseModel):
    """Only server configuration can select a catalog entry."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    workflow_id: str | None = None
    workflow_default_id: str | None = None
    workflow_version: str | None = None
    workflow_snapshot: str | None = None


class PlanningWorkflowState(AgentState):
    """Keep optional Plan conversations independent of execution checkpoints."""

    planning_workflow_snapshot: NotRequired[Annotated[str, reduce_workflow_snapshot, OmitFromInput]]


class PlanningWorkflowSelection(BaseModel):
    """Planning configuration never consumes an operational workflow pin."""

    model_config = ConfigDict(frozen=True, extra="ignore")
    planning_workflow_id: str | None = None
    planning_workflow_version: str | None = None
    planning_workflow_snapshot: str | None = None


def _require_purpose(
    snapshot: WorkflowSnapshot, purpose: Literal["execution", "planning"]
) -> WorkflowSnapshot:
    if snapshot.document.metadata.purpose != purpose:
        raise WorkflowError(f"{snapshot.id} is not a {purpose} workflow")
    return snapshot


def resolve_workflow(
    catalog: WorkflowCatalog,
    selection: WorkflowSelection,
    checkpoint: str | None,
    *,
    purpose: Literal["execution", "planning"] = "execution",
) -> WorkflowSnapshot:
    """Keep checkpoint content even when the registered source later changes."""
    if checkpoint and selection.workflow_snapshot and checkpoint != selection.workflow_snapshot:
        raise WorkflowError("configured workflow pin differs from checkpoint")
    pin = checkpoint or selection.workflow_snapshot
    if pin:
        snapshot = WorkflowSnapshot.model_validate_json(pin)
        if selection.workflow_id is not None and selection.workflow_id != snapshot.id:
            raise WorkflowError("configured workflow ID differs from checkpoint")
        if (
            selection.workflow_version is not None
            and selection.workflow_version != snapshot.version
        ):
            raise WorkflowError("configured workflow version differs from checkpoint")
        return _require_purpose(snapshot, purpose)
    if selection.workflow_version is not None and selection.workflow_id is None:
        raise WorkflowError("workflow_version requires workflow_id")
    snapshot = catalog.resolve(
        selection.workflow_id
        if selection.workflow_id is not None
        else (
            selection.workflow_default_id
            if selection.workflow_default_id is not None
            else "red-team"
        ),
        selection.workflow_version,
    )
    return _require_purpose(snapshot, purpose)


def _inject_contract(
    request: ModelRequest,
    pin: str | None,
    *,
    role: str,
    purpose: Literal["execution", "planning"],
) -> ModelRequest:
    if pin is None:
        raise WorkflowError("workflow checkpoint pin is missing")
    snapshot = _require_purpose(WorkflowSnapshot.model_validate_json(pin), purpose)
    contract = snapshot.document.compile(role)
    blocks = list(request.system_message.content_blocks) if request.system_message else []
    blocks.append({"type": "text", "text": contract})
    return request.override(system_message=SystemMessage(content_blocks=blocks))


class WorkflowMiddleware(AgentMiddleware[AgentState]):
    """Shared runtime contract without rebuilding or mutating specialist prompts."""

    state_schema = WorkflowState

    def __init__(self, *, role: str, catalog: WorkflowCatalog | None = None) -> None:
        super().__init__()
        self.role = role
        self.catalog = catalog if catalog is not None else WorkflowCatalog.default()

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, JsonValue]:
        selection = WorkflowSelection.model_validate(get_config().get("configurable", {}))
        snapshot = resolve_workflow(self.catalog, selection, state.get("workflow_snapshot"))
        if not state.get("workflow_snapshot") and not selection.workflow_snapshot:
            if LegacyPlan.model_validate(state).unbound_checkpoint:
                if selection.workflow_version is not None:
                    raise WorkflowError(
                        "explicit workflow version cannot upgrade a legacy checkpoint"
                    )
                snapshot = legacy_snapshot(snapshot)
        return {"workflow_snapshot": snapshot.model_dump_json()}

    async def abefore_agent(self, state: AgentState, runtime: Runtime) -> dict[str, JsonValue]:
        return self.before_agent(state, runtime)

    def _inject(self, request: ModelRequest) -> ModelRequest:
        pin = WorkflowSelection.model_validate(request.state).workflow_snapshot
        return _inject_contract(request, pin, role=self.role, purpose="execution")

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        return handler(self._inject(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._inject(request))


class PlanningWorkflowMiddleware(WorkflowMiddleware):
    """Inject the selected Plan playbook without OPPLAN or execution inheritance."""

    state_schema = PlanningWorkflowState

    def __init__(self, *, catalog: WorkflowCatalog | None = None) -> None:
        super().__init__(role="soundwave", catalog=catalog)

    def before_agent(self, state: AgentState, runtime: Runtime) -> dict[str, JsonValue]:
        selected = PlanningWorkflowSelection.model_validate(get_config().get("configurable", {}))
        selection = WorkflowSelection(
            workflow_id=selected.planning_workflow_id,
            workflow_default_id="planning",
            workflow_version=selected.planning_workflow_version,
            workflow_snapshot=selected.planning_workflow_snapshot,
        )
        snapshot = resolve_workflow(
            self.catalog,
            selection,
            state.get("planning_workflow_snapshot"),
            purpose="planning",
        )
        return {"planning_workflow_snapshot": snapshot.model_dump_json()}

    def _inject(self, request: ModelRequest) -> ModelRequest:
        pin = PlanningWorkflowSelection.model_validate(request.state).planning_workflow_snapshot
        return _inject_contract(request, pin, role="soundwave", purpose="planning")
