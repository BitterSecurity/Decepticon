from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from decepticon.agents.standard.soundwave import _assert_planning_tools
from decepticon.middleware.filesystem import FilesystemMiddleware


def planning_filesystem() -> FilesystemMiddleware:
    return FilesystemMiddleware(backend=SimpleNamespace(), plan_only=True)


def test_interview_accepts_only_planning_and_filesystem_tools() -> None:
    _assert_planning_tools(
        [
            SimpleNamespace(name="ask_user_question"),
            SimpleNamespace(name="complete_engagement_planning"),
        ],
        [planning_filesystem()],
    )


@pytest.mark.parametrize("name", ["bash", "execute", "browser_action", "task"])
def test_interview_rejects_execution_tools(name: str) -> None:
    with pytest.raises(ValueError, match="Plan mode"):
        _assert_planning_tools([SimpleNamespace(name=name)], [planning_filesystem()])


def test_interview_rejects_unscoped_filesystem_tool() -> None:
    with pytest.raises(ValueError, match="plan-scoped middleware"):
        _assert_planning_tools([SimpleNamespace(name="read_file")], [planning_filesystem()])


def test_interview_rejects_unscoped_filesystem_middleware() -> None:
    with pytest.raises(ValueError, match="plan-scoped filesystem"):
        _assert_planning_tools([], [FilesystemMiddleware(backend=SimpleNamespace())])


def test_interview_rejects_extra_unscoped_filesystem_middleware() -> None:
    with pytest.raises(ValueError, match="plan-scoped middleware"):
        _assert_planning_tools(
            [], [planning_filesystem(), FilesystemMiddleware(backend=SimpleNamespace())]
        )


def test_completed_plan_emits_draft_event_without_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = import_module("decepticon.tools.interaction.complete_planning")
    events: list[dict[str, str]] = []
    monkeypatch.setattr(module, "_runtime_context", lambda: (str(tmp_path), "", True))
    monkeypatch.setattr(module, "validate_planning_bundle", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "planning_bundle_digest", lambda *args: "digest")
    monkeypatch.setattr(module, "_safe_writer", lambda: events.append)

    response = module.complete_engagement_planning.func(tool_call_id="call-1")

    assert events == [{"type": "planning_draft_ready", "agent": "soundwave", "id": "call-1"}]
    assert "Review the documents" in response
    assert (tmp_path / ".planning-draft-ready").read_text() == "digest"


def test_planning_digest_matches_cli_and_launcher(tmp_path: Path) -> None:
    module = import_module("decepticon.tools.interaction.complete_planning")
    plan = tmp_path / "plan"
    plan.mkdir()
    for filename in (
        "roe.json",
        "threat-profile.json",
        "conops.json",
        "deconfliction.json",
        "contact.json",
        "data-handling.json",
        "abort.json",
        "cleanup.json",
    ):
        (plan / filename).write_text("{}")

    assert module.planning_bundle_digest(tmp_path) == (
        "23bb0ad057e96e5098d6c8ef731ce1afadd9e1957f6adf6b720196ad35409418"
    )


def test_trusted_document_source_preserves_safety_override_guard(monkeypatch):
    from decepticon.agents.build import SafetyOverrideViolation, build_tools
    from decepticon.tools.interaction.complete_planning import create_complete_planning_tool

    monkeypatch.delenv("DECEPTICON_ALLOW_SAFETY_OVERRIDES", raising=False)
    native = create_complete_planning_tool(lambda runtime: None)
    with pytest.raises(SafetyOverrideViolation):
        build_tools(
            role="soundwave",
            standard_tools={"complete_engagement_planning": native},
            overrides={
                "complete_engagement_planning": SimpleNamespace(name="complete_engagement_planning")
            },
        )


def test_factory_composes_document_source_without_disabling_guards(monkeypatch):
    module = import_module("decepticon.agents.standard.soundwave")
    monkeypatch.delenv("DECEPTICON_ALLOW_SAFETY_OVERRIDES", raising=False)
    captured = {}

    def create_agent(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(with_config=lambda config: "compiled-fixture")

    monkeypatch.setattr(module, "create_agent", create_agent)
    monkeypatch.setattr(module, "load_plugin_callbacks", lambda **kwargs: [])
    result = module.create_soundwave_agent(
        backend=SimpleNamespace(),
        llm=SimpleNamespace(),
        fallback_models=[],
        planning_document_source=lambda runtime: None,
        middleware=[planning_filesystem()],
        system_prompt="owned fixture",
    )
    assert result == "compiled-fixture"
    completion = next(
        tool for tool in captured["tools"] if tool.name == "complete_engagement_planning"
    )
    assert completion is not module._STANDARD_TOOLS["complete_engagement_planning"]
    assert (
        module._STANDARD_TOOLS["complete_engagement_planning"]
        is module.complete_engagement_planning
    )
    with pytest.raises(ValueError, match="native planning tool set"):
        module.create_soundwave_agent(
            backend=SimpleNamespace(),
            llm=SimpleNamespace(),
            fallback_models=[],
            planning_document_source=lambda runtime: None,
            tools=[],
            middleware=[planning_filesystem()],
        )
