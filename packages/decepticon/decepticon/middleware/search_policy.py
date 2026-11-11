"""Checkpoint contracts for evidence-driven OPPLAN search."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from decepticon_core.types.engagement import ACTIONABLE_OBJECTIVE_STATUSES, ObjectiveStatus

Disposition = Literal[
    "goal_met", "budget_exhausted", "search_exhausted", "blocked", "operator_stopped"
]


class SearchContractError(ValueError):
    """A search transition violates its checkpoint contract."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class SearchSession(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    thread_id: str
    run_id: str
    started_at: str
    status: Literal["searching", "terminal"] = "searching"
    plan_revision: int = 0
    max_dispatches: Annotated[int, Field(gt=0)] | None = None
    reviewed_task_ids: tuple[str, ...] = ()
    reviewed_plan_revision: int = 0
    disposition: Disposition | None = None
    reason: str = ""
    final_attempts: int = 0


class SearchReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: Literal[2] = 2
    thread_id: str
    run_id: str
    plan_revision: int
    disposition: Disposition
    reason: str
    goal_finding_uuids: tuple[str, ...]
    report_document_uuid: str
    report_revision: int
    report_sha256: str


class SearchObjective(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    id: str
    status: ObjectiveStatus
    parent_id: str | None = None
    blocked_by: tuple[str, ...] = ()
    any_of: tuple[tuple[str, ...], ...] = ()
    required_fact_ids: tuple[str, ...] = ()
    outcome: str | None = None


def merge_search_events(
    left: dict[str, str] | None, right: dict[str, str] | None
) -> dict[str, str]:
    """Merge independent task receipts without losing parallel returns."""
    merged = dict(left or {})
    for key, value in (right or {}).items():
        if key in merged and merged[key] != value:
            raise SearchContractError("task event identity cannot be reassigned")
        merged[key] = value
    return merged


def current_events(session: SearchSession, events: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in events.items() if key.startswith(session.run_id + ":")}


def review_plan(
    session: SearchSession, events: Mapping[str, str], plan_revision: int
) -> SearchSession:
    """Acknowledge returned work only against a current, saved OPPLAN revision."""
    if plan_revision < session.reviewed_plan_revision:
        raise SearchContractError("search review cannot use an older OPPLAN")
    return session.model_copy(
        update={
            "reviewed_task_ids": tuple(current_events(session, events)),
            "reviewed_plan_revision": plan_revision,
            "plan_revision": plan_revision,
            "final_attempts": 0,
        }
    )


def ready_objectives(
    objectives: tuple[SearchObjective, ...], facts: tuple[dict[str, JsonValue], ...]
) -> tuple[str, ...]:
    """Identify unfinished executable leaves without changing OPPLAN ownership."""
    by_id = {row.id: row for row in objectives}
    parents = {row.parent_id for row in objectives}
    verified = {
        str(row["id"]) for row in facts if row.get("verified") is True and row.get("evidence_refs")
    }
    return tuple(
        row.id
        for row in objectives
        if row.id not in parents
        and row.status in ACTIONABLE_OBJECTIVE_STATUSES
        and all(key in by_id and by_id[key].status == "completed" for key in row.blocked_by)
        and all(
            any(key in by_id and by_id[key].status == "completed" for key in group)
            for group in row.any_of
        )
        and set(row.required_fact_ids) <= verified
    )
