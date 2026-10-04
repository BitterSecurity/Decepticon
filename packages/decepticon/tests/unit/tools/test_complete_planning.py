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
