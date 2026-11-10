"""Tests for the Soundwave/Autohunt completion gate."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from decepticon.tools.interaction.complete_planning import validate_planning_bundle


def _source_tool(root, monkeypatch, transform=None):
    module = import_module("decepticon.tools.interaction.complete_planning")
    events = []
    calls = []

    class Source:
        def read_document(self, filename):
            calls.append(filename)
            content = (root / "plan" / filename).read_bytes()
            result = (content, "revision-1")
            return transform(filename, result, len(calls)) if transform else result

    monkeypatch.setattr(module, "_safe_writer", lambda: events.append)
    tool = module.create_complete_planning_tool(lambda runtime: Source())
    runtime = SimpleNamespace(
        config={
            "configurable": {
                "target_value": "https://target.example",
                "authorization_confirmed": True,
            }
        }
    )
    return tool, runtime, events, calls


def test_remote_source_keeps_native_validation_and_event_ownership(tmp_path, monkeypatch):
    _write_bundle(tmp_path)
    tool, runtime, events, calls = _source_tool(tmp_path, monkeypatch)
    assert "ready. Review" in tool.func(runtime=runtime, tool_call_id="source-call")
    assert len(calls) == 16 and len(set(calls)) == 8
    assert events[0]["type"] == "planning_draft_ready"
    assert events[0]["agent"] == "soundwave"
    assert events[0]["id"] == "source-call"
    assert len(events[0]["bundle_digest"]) == 64
    assert not (tmp_path / ".planning-draft-ready").exists()
    assert "runtime" not in tool.tool_call_schema.model_json_schema().get("properties", {})


@pytest.mark.parametrize(
    "failure",
    ["authorization", "target", "missing", "malformed", "oversize", "revision", "utf8", "changed"],
)
def test_remote_source_refuses_invalid_or_changing_bundles(tmp_path, monkeypatch, failure):
    _write_bundle(tmp_path)
    if failure == "missing":
        (tmp_path / "plan/cleanup.json").unlink()
    if failure == "malformed":
        (tmp_path / "plan/conops.json").write_text("{")

    def transform(filename, result, call):
        if filename == "roe.json":
            if failure == "oversize":
                return (b"x" * (512 * 1024 + 1), result[1])
            if failure == "revision":
                return (result[0], "")
            if failure == "utf8":
                return (b"\xff", result[1])
            if failure == "changed" and call > 8:
                return (result[0], "revision-2")
        return result

    tool, runtime, events, _ = _source_tool(tmp_path, monkeypatch, transform)
    if failure == "authorization":
        runtime.config["configurable"]["authorization_confirmed"] = False
    if failure == "target":
        runtime.config["configurable"]["target_value"] = "https://outside.example"
    assert "ready. Review" not in tool.func(runtime=runtime)
    assert events == []


def test_source_failure_does_not_fall_back_to_host_files_or_expose_credentials(monkeypatch):
    module = import_module("decepticon.tools.interaction.complete_planning")

    def source(runtime):
        raise RuntimeError("private-fixture-token-and-url")

    monkeypatch.setattr(module, "_runtime_context", lambda: pytest.fail("must not fall back"))
    result = module.create_complete_planning_tool(source).func(runtime=SimpleNamespace(config={}))
    assert "could not be confirmed" in result
    assert "private-fixture-token-and-url" not in result


def test_absent_source_preserves_explicit_local_completion(tmp_path, monkeypatch):
    module = import_module("decepticon.tools.interaction.complete_planning")
    _write_bundle(tmp_path)
    monkeypatch.setattr(
        module, "_runtime_context", lambda: (str(tmp_path), "https://target.example", True)
    )
    monkeypatch.setattr(module, "_safe_writer", lambda: None)
    result = module.create_complete_planning_tool(lambda runtime: None).func(
        runtime=SimpleNamespace(config={})
    )
    assert "ready. Review" in result
    assert (tmp_path / ".planning-draft-ready").exists()


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
