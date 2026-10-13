"""Tests for the Soundwave/Autohunt completion gate."""

from __future__ import annotations

import json
from pathlib import Path

from decepticon.tools.interaction.complete_planning import validate_planning_bundle


def _write_bundle(root: Path, *, target: str = "https://target.example") -> None:
    plan = root / "plan"
    plan.mkdir()
    documents = {
        "roe.json": {
            "engagement_name": "autohunt-demo",
            "client": "Demo",
            "start_date": "2026-08-26",
            "end_date": "2026-08-27",
            "engagement_type": "external",
            "testing_window": "24/7",
            "in_scope": [{"target": target, "type": "web_url"}],
            "authorization_reference": "ROE-2026-812",
        },
        "threat-profile.json": {
            "engagement_name": "autohunt-demo",
            "actor_name": "External tester",
            "tier": "tier-1",
            "sophistication": "low",
            "motivation": "validation",
        },
        "conops.json": {
            "engagement_name": "autohunt-demo",
            "executive_summary": "Validate the declared target only.",
        },
        "deconfliction.json": {"engagement_name": "autohunt-demo"},
        "contact.json": {
            "engagement_name": "autohunt-demo",
            "primary_operator": {
                "name": "Operator",
                "role": "Primary Operator",
                "channel": "local:console",
            },
        },
        "data-handling.json": {"engagement_name": "autohunt-demo"},
        "abort.json": {"engagement_name": "autohunt-demo"},
        "cleanup.json": {"engagement_name": "autohunt-demo"},
    }
    for filename, payload in documents.items():
        (plan / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_validate_planning_bundle_accepts_complete_authorized_bundle(tmp_path: Path) -> None:
    _write_bundle(tmp_path)

    assert (
        validate_planning_bundle(
            tmp_path,
            target_value="https://target.example",
            authorization_confirmed=True,
        )
        is None
    )


def test_validate_planning_bundle_rejects_unconfirmed_authorization(tmp_path: Path) -> None:
    _write_bundle(tmp_path)

    assert validate_planning_bundle(tmp_path, authorization_confirmed=False) == (
        "Authorization is not confirmed; do not mark the draft ready."
    )


def test_validate_planning_bundle_rejects_declared_target_outside_roe(tmp_path: Path) -> None:
    _write_bundle(tmp_path)

    assert validate_planning_bundle(tmp_path, target_value="https://other.example") == (
        "RoE in_scope must contain the exact launcher-declared target."
    )


def test_validate_planning_bundle_reports_all_invalid_documents(tmp_path: Path) -> None:
    _write_bundle(tmp_path)
    plan = tmp_path / "plan"
    (plan / "threat-profile.json").unlink()
    (plan / "conops.json").write_text("{", encoding="utf-8")
    (plan / "abort.json").write_text(
        json.dumps({"engagement_name": "autohunt-demo", "hallucination_threshold": 0}),
        encoding="utf-8",
    )
    (plan / "cleanup.json").write_text(
        json.dumps({"engagement_name": "autohunt-demo", "artifacts": [{}]}), encoding="utf-8"
    )

    failure = validate_planning_bundle(tmp_path)

    assert failure is not None
    for filename in ("threat-profile.json", "conops.json", "abort.json", "cleanup.json"):
        assert f"plan/{filename}" in failure
    assert "hallucination_threshold" in failure
    assert "removal_command" in failure


def test_complete_planning_does_not_mark_invalid_bundle_ready(tmp_path: Path) -> None:
    from decepticon.tools.interaction.complete_planning import complete_engagement_planning

    _write_bundle(tmp_path)
    (tmp_path / "plan/conops.json").unlink()
    (tmp_path / "plan/contact.json").unlink()

    response = complete_engagement_planning.invoke(
        {
            "name": "complete_engagement_planning",
            "args": {},
            "id": "planning-test",
            "type": "tool_call",
        },
        config={"configurable": {"workspace_path": str(tmp_path)}},
    )

    assert response.content.startswith("Planning draft blocked:")
    assert "plan/conops.json" in response.content
    assert "plan/contact.json" in response.content
    assert not (tmp_path / ".planning-draft-ready").exists()
