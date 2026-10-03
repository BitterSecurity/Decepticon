from __future__ import annotations

import json
from pathlib import Path

from decepticon.agents import build
from decepticon.agents.standard import blue_cell as agent_mod
from decepticon_core.contracts.slots import SLOTS_PER_ROLE, MiddlewareSlot

_REPO_ROOT = Path(__file__).resolve().parents[5]


def test_role_registered_as_readonly_base_slots() -> None:
    slots = SLOTS_PER_ROLE["blue_cell"]
    assert MiddlewareSlot.SANDBOX_NOTIFICATION not in slots
    assert MiddlewareSlot.HITL_APPROVAL not in slots
    assert MiddlewareSlot.FILESYSTEM not in slots
    assert MiddlewareSlot.EVENT_LOG not in slots
    assert MiddlewareSlot.UNTRUSTED_OUTPUT in slots
    assert MiddlewareSlot.PROMPT_INJECTION_SHIELD in slots
    assert MiddlewareSlot.SUMMARIZATION in slots
    assert MiddlewareSlot.PATCH_TOOL_CALLS in slots


def test_standard_tools_are_readonly() -> None:
    names = set(agent_mod._STANDARD_TOOLS)
    assert names == {"blue_sensor_scan", "blue_sensor_events", "blue_sensor_body"}
    assert "blue_cell_scan" not in names
    assert "bash" not in names
    assert "kg_add_node" not in names
    assert "kg_add_edge" not in names


def test_build_tools_resolves_role_without_attack_tools() -> None:
    tools = build.build_tools(role="blue_cell", standard_tools=agent_mod._STANDARD_TOOLS)
    names = {t.name for t in tools}
    assert "blue_sensor_scan" in names
    assert "blue_sensor_events" in names
    assert "bash" not in names


def test_subagent_spec_targets_orchestrator() -> None:
    spec = agent_mod.SUBAGENT_SPEC
    assert spec.name == "blue_cell"
    assert spec.factory is agent_mod.create_blue_cell_agent
    assert spec.parent_agents == ("decepticon",)
    assert spec.bundle == "standard"


def test_langgraph_registers_blue_cell_graph() -> None:
    config = json.loads((_REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert (
        config["graphs"]["blue_cell"]
        == "./packages/decepticon/decepticon/agents/standard/blue_cell.py:graph"
    )


def test_blue_cell_published_as_decepticon_subagent() -> None:
    """The orchestrator only delegates to specs published under the
    ``decepticon.subagents`` entry-point group (load_subagents_for_parent).
    Without this line Blue Cell is graph-served but never delegated — the
    Defense Brief workflow it advertises is unreachable. Parsed from pyproject
    so it is independent of editable-install metadata refresh."""
    import tomllib

    pyproject = _REPO_ROOT / "packages" / "decepticon" / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    subagents = data["project"]["entry-points"]["decepticon.subagents"]
    assert subagents.get("blue_cell") == "decepticon.agents.standard.blue_cell:SUBAGENT_SPEC"
