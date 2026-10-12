from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_PROMPTS = _REPO_ROOT / "packages/decepticon/decepticon/agents/prompts/standard"
_REVERSER_AGENT = _REPO_ROOT / "packages/decepticon/decepticon/agents/standard/reverser.py"

_REVERSER_SKILL = _REPO_ROOT / "packages/decepticon/decepticon/skills/standard/reverser/SKILL.md"
_VIRTUALIZED_SKILL = (
    _REPO_ROOT
    / "packages/decepticon/decepticon/skills/standard/reverser/virtualized-protectors/SKILL.md"
)


def test_orchestrator_dispatches_reverser_for_no_workload_triage() -> None:
    prompt = (_PROMPTS / "decepticon.md").read_text()

    assert "Basic triage / Radare2 work still goes to `reverser`" in prompt
    assert "uses the REA/Ghidra workload for native analysis" in prompt
    assert 'Do NOT block binary triage just because `ops_start("reversing")` fails' in prompt


def test_reverser_prompt_has_radare2_fallback_path() -> None:
    prompt = (_PROMPTS / "reverser.md").read_text()

    assert "RADARE2" in prompt
    assert "bin_r2_script" in prompt
    assert "Radare2/r2" in prompt


def test_reverser_spec_advertises_basic_triage_without_workload() -> None:
    agent_src = _REVERSER_AGENT.read_text()

    assert "Radare2-assisted basic triage without the reversing workload" in agent_src


def test_reverser_exposes_rea_instead_of_legacy_ghidra_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decepticon.agents.standard import reverser

    selected: list[set[str]] = []

    class Graph:
        def with_config(self, _config: object) -> Graph:
            return self

    def capture_agent(_llm: object, *, tools: list[object], **_kwargs: object) -> Graph:
        selected.append({tool.name for tool in tools})
        return Graph()

    monkeypatch.setattr(reverser, "create_agent", capture_agent)
    monkeypatch.setattr(reverser, "set_sandbox", lambda _sandbox: None)
    monkeypatch.setattr(reverser, "load_plugin_callbacks", lambda **_kwargs: [])
    args = {
        "llm": object(),
        "fallback_models": [],
        "backend": object(),
        "sandbox": object(),
        "middleware": [],
        "system_prompt": "test",
    }

    monkeypatch.setenv("DECEPTICON_REA_URL", "http://rea-mcp:8082")
    reverser.create_reverser_agent(**args)
    assert {"rea_status", "rea_find_tools", "rea_call_tool"} <= selected[-1]
    assert not {"ghidra_analyze", "ghidra_decompile", "ghidra_xrefs"} & selected[-1]

    monkeypatch.delenv("DECEPTICON_REA_URL")
    reverser.create_reverser_agent(**args)
    assert {"ghidra_analyze", "ghidra_decompile", "ghidra_xrefs"} <= selected[-1]
    assert not {"rea_status", "rea_find_tools", "rea_call_tool"} & selected[-1]


def test_reverser_skill_catalog_names_radare2_fallback() -> None:
    skill = _REVERSER_SKILL.read_text()

    assert "Radare2" in skill
    assert "bin_r2_script" in skill


def test_reverser_routes_vmprotect_vmp2_themida_workflow() -> None:
    prompt = (_PROMPTS / "reverser.md").read_text()
    catalog = _REVERSER_SKILL.read_text()

    assert "VMProtect / VMP2 / Themida" in prompt
    assert "/skills/standard/reverser/virtualized-protectors/SKILL.md" in catalog
    assert "VMProtect / VMP2 / Themida" in catalog


def test_virtualized_protectors_skill_captures_backengineering_workflow() -> None:
    skill = _VIRTUALIZED_SKILL.read_text()

    assert "Back Engineering Labs" in skill
    assert "VMProtect 2" in skill
    assert "Themida" in skill
    assert "VMEnter" in skill
    assert "VIP" in skill
    assert "VMEXIT" in skill
    assert "Avoid brittle VM-handler pattern matching" in skill
    assert "incremental lifting and control-flow recovery" in skill
