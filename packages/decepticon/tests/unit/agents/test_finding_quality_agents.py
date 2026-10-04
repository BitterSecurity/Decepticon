"""The default red-team orchestrator can discover and build quality stages."""

from __future__ import annotations

from types import SimpleNamespace

import langchain.agents
from langchain_core.runnables import RunnableLambda

from decepticon.agents.middleware_slots import _make_subagent
from decepticon.agents.standard import finding_reporter, finding_verifier
from decepticon_core.contracts.slots import SLOTS_PER_ROLE, MiddlewareSlot


def test_quality_subagent_specs_are_standard():
    assert finding_verifier.SUBAGENT_SPEC.parent_agents == ("decepticon",)
    assert finding_reporter.SUBAGENT_SPEC.parent_agents == ("decepticon",)
    assert finding_verifier.SUBAGENT_SPEC.bundle == "standard"
    assert finding_reporter.SUBAGENT_SPEC.bundle == "standard"
    assert MiddlewareSlot.SANDBOX_NOTIFICATION not in SLOTS_PER_ROLE["finding_reporter"]


def test_reporter_uses_cvss_and_mitre_without_shell(monkeypatch):
    captured = {}

    def fake_create_agent(_llm, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(with_config=lambda _config: captured)

    monkeypatch.setattr(langchain.agents, "create_agent", fake_create_agent)
    finding_reporter.create_finding_reporter(
        llm=object(),
        fallback_models=[],
        backend=object(),
        sandbox=object(),
        middleware=[],
        system_prompt="report",
    )
    names = {tool.name for tool in captured["tools"]}
    assert "cvss_score" in names
    assert "check_finding_report" in names
    assert "mitre_lookup_technique" in names
    assert "bash" not in names


def test_only_decepticon_task_schema_requires_opplan_binding():
    subagents = [
        {
            "name": "recon",
            "description": "recon",
            "runnable": RunnableLambda(lambda state: state),
        }
    ]
    orchestrator = _make_subagent(backend=object(), subagents=subagents, role="decepticon")
    other = _make_subagent(backend=object(), subagents=subagents, role="vulnresearch")
    assert {"task_id", "plan_revision"} <= set(orchestrator.tools[0].args)
    assert "task_id" not in other.tools[0].args
