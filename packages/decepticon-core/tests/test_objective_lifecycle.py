"""The OPPLAN objective lifecycle is declared once, in ``decepticon_core``.

These tests pin the declared model to the rules the framework enforced before
the model existed (string tables and status sets repeated across the OPPLAN
tools, graph inspection, workflow gates and search policy). Changing a rule is
a deliberate edit to the model and to the expectations here.
"""

from __future__ import annotations

import pytest

from decepticon_core.types.engagement import (
    ACTIONABLE_OBJECTIVE_STATUSES,
    OBJECTIVE_INVALIDATION_TRANSITIONS,
    OBJECTIVE_STATUS_TRANSITIONS,
    PREREQUISITE_BOUND_OBJECTIVE_STATUSES,
    RESOLVED_OBJECTIVE_STATUSES,
    ObjectiveStatus,
    can_transition_objective,
    next_objective_statuses,
)

# The rules as they were written before the model, verbatim.
LEGACY_REQUESTED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"in-progress", "cancelled"},
    "in-progress": {"completed", "blocked", "cancelled"},
    "blocked": {"in-progress", "completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
LEGACY_RESOLVED = {"completed", "cancelled"}
LEGACY_ACTIONABLE = {"pending", "in-progress"}
LEGACY_PREREQUISITE_BOUND = {"in-progress", "completed"}


def _values(statuses: frozenset[ObjectiveStatus]) -> set[str]:
    return {status.value for status in statuses}


def test_requested_transitions_match_the_legacy_rules() -> None:
    declared = {
        status.value: _values(targets) for status, targets in OBJECTIVE_STATUS_TRANSITIONS.items()
    }
    assert declared == LEGACY_REQUESTED_TRANSITIONS


def test_model_covers_every_status_exactly_once() -> None:
    assert set(OBJECTIVE_STATUS_TRANSITIONS) == set(ObjectiveStatus)


def test_no_status_transitions_to_itself() -> None:
    for status, targets in OBJECTIVE_STATUS_TRANSITIONS.items():
        assert status not in targets


def test_resolved_statuses_are_final_for_requests() -> None:
    for status in RESOLVED_OBJECTIVE_STATUSES:
        assert OBJECTIVE_STATUS_TRANSITIONS[status] == frozenset()
        assert next_objective_statuses(status) == ()


def test_classifications_match_the_legacy_sets() -> None:
    assert _values(RESOLVED_OBJECTIVE_STATUSES) == LEGACY_RESOLVED
    assert _values(ACTIONABLE_OBJECTIVE_STATUSES) == LEGACY_ACTIONABLE
    assert _values(PREREQUISITE_BOUND_OBJECTIVE_STATUSES) == LEGACY_PREREQUISITE_BOUND


def test_blocked_is_neither_actionable_nor_resolved() -> None:
    assert not ACTIONABLE_OBJECTIVE_STATUSES & RESOLVED_OBJECTIVE_STATUSES
    assert (
        ObjectiveStatus.BLOCKED not in ACTIONABLE_OBJECTIVE_STATUSES | RESOLVED_OBJECTIVE_STATUSES
    )


def test_invalidation_reopens_exactly_the_prerequisite_bound_statuses_as_blocked() -> None:
    assert set(OBJECTIVE_INVALIDATION_TRANSITIONS) == PREREQUISITE_BOUND_OBJECTIVE_STATUSES
    for targets in OBJECTIVE_INVALIDATION_TRANSITIONS.values():
        assert targets == frozenset({ObjectiveStatus.BLOCKED})
    # This is the only way a completed objective changes status again.
    assert ObjectiveStatus.COMPLETED in OBJECTIVE_INVALIDATION_TRANSITIONS
    assert not can_transition_objective("completed", "blocked")


def test_helpers_accept_plain_strings_and_refuse_unknown_values() -> None:
    assert can_transition_objective("pending", "in-progress")
    assert not can_transition_objective("pending", "completed")
    assert not can_transition_objective("bogus", "in-progress")
    assert not can_transition_objective("pending", "bogus")
    assert next_objective_statuses("in-progress") == (
        ObjectiveStatus.BLOCKED,
        ObjectiveStatus.CANCELLED,
        ObjectiveStatus.COMPLETED,
    )
    assert next_objective_statuses("bogus") == ()


def test_status_sets_match_plain_string_statuses() -> None:
    # Checkpointed OPPLAN rows carry plain strings; membership must still hold.
    assert "completed" in RESOLVED_OBJECTIVE_STATUSES
    assert "pending" in ACTIONABLE_OBJECTIVE_STATUSES
    assert None not in RESOLVED_OBJECTIVE_STATUSES


def test_declared_model_is_read_only() -> None:
    with pytest.raises(TypeError):
        OBJECTIVE_STATUS_TRANSITIONS[ObjectiveStatus.COMPLETED] = frozenset(  # type: ignore[index]
            {ObjectiveStatus.PENDING}
        )
    with pytest.raises(TypeError):
        OBJECTIVE_INVALIDATION_TRANSITIONS[ObjectiveStatus.PENDING] = frozenset()  # type: ignore[index]
