"""Anti-drift guards for the agent registries.

Three independent registries must stay in lock-step or an agent silently
half-wires: the ``decepticon.subagents`` entry-point group (what the
orchestrator can dispatch), the LangGraph manifests (what can be served
as a standalone graph), and the role tables in ``decepticon_core``
(slots + model tiers). The ``phisher`` / ``mobile_operator`` /
``wireless_operator`` agents shipped registered as subagents but absent
from ``graph_registry`` / ``langgraph.json`` — these tests would have
failed on that drift; keep them green.
"""

from __future__ import annotations

import json
from importlib import import_module
from importlib.metadata import entry_points
from pathlib import Path

import pytest
import yaml
from langchain_core.runnables import Runnable

import decepticon
from decepticon.graph_registry import BUILTIN_GRAPHS, STANDARD_GRAPHS
from decepticon.middleware.skillogy import _PHASE_FOR_ROLE
from decepticon.skillogy.builder.seeds import load_asset_types
from decepticon_core.contracts.slots import SLOTS_PER_ROLE
from decepticon_core.types.llm import AGENT_TIERS


def _find_repo_file(name: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / name
        if candidate.exists():
            return candidate
    raise FileNotFoundError(name)


def _subagent_entry_point_names() -> list[str]:
    return sorted(ep.name for ep in entry_points(group="decepticon.subagents"))


def test_every_registered_subagent_has_a_graph():
    """Each ``decepticon.subagents`` entry point must have a built-in graph.

    Catches the original defect: a specialist registered as a dispatchable
    subagent but missing from ``graph_registry`` (so it can never be served
    as a standalone LangGraph assistant).
    """
    missing = [name for name in _subagent_entry_point_names() if name not in BUILTIN_GRAPHS]
    assert not missing, f"subagents without a built-in graph: {missing}"


def test_every_slot_role_has_a_model_tier():
    """Every role in ``SLOTS_PER_ROLE`` must have an ``AGENT_TIERS`` entry.

    ``_resolve_tier`` does ``AGENT_TIERS[role]`` — a missing tier is a hard
    KeyError the first time the agent's model is built.
    """
    missing = sorted(set(SLOTS_PER_ROLE) - set(AGENT_TIERS))
    assert not missing, f"roles with slots but no model tier: {missing}"


def test_langgraph_manifest_covers_standard_graphs():
    """The shipped ``langgraph.json`` must list *exactly* the standard graphs.

    Equality (not subset) so a stale manifest entry left behind after a rename
    is caught too, not only a missing one.
    """
    manifest = json.loads(_find_repo_file("langgraph.json").read_text(encoding="utf-8"))
    served = set(manifest["graphs"])
    expected = set(STANDARD_GRAPHS)
    missing = sorted(expected - served)
    extra = sorted(served - expected)
    assert not missing, f"standard graphs absent from langgraph.json: {missing}"
    assert not extra, f"langgraph.json lists graphs not in STANDARD_GRAPHS: {extra}"


@pytest.mark.parametrize("graph_name", STANDARD_GRAPHS)
def test_langgraph_manifest_exports_runnable(graph_name: str) -> None:
    manifest = json.loads(_find_repo_file("langgraph.json").read_text(encoding="utf-8"))
    module_path, export_name = manifest["graphs"][graph_name].rsplit(":", 1)
    module_name = (
        Path(module_path)
        .relative_to("packages/decepticon")
        .with_suffix("")
        .as_posix()
        .replace("/", ".")
    )
    exported_graph = getattr(import_module(module_name), export_name)
    assert isinstance(exported_graph, Runnable)


@pytest.mark.parametrize(
    "role",
    ["phisher", "mobile_operator", "wireless_operator", "osint_operator", "iot_operator"],
)
def test_known_specialists_fully_wired(role):
    """Spot-check the specialists this guard was introduced for."""
    assert role in SLOTS_PER_ROLE
    assert role in AGENT_TIERS
    assert role in STANDARD_GRAPHS
    assert role in _subagent_entry_point_names()


_PHASES_YAML = (
    Path(decepticon.__file__).resolve().parent / "skillogy" / "builder" / "seeds" / "phases.yaml"
)


def _seeded_phase_names() -> set[str]:
    data = yaml.safe_load(_PHASES_YAML.read_text(encoding="utf-8"))
    return {phase["name"] for phase in data["phases"]}


def test_skillogy_phase_values_are_seeded_phases():
    """Every ``_PHASE_FOR_ROLE`` value must be a real seeded ``:Phase`` node."""
    valid = _seeded_phase_names()
    bad = {role: phase for role, phase in _PHASE_FOR_ROLE.items() if phase not in valid}
    assert not bad, f"_PHASE_FOR_ROLE values not seeded in phases.yaml: {bad}"


def test_every_standard_graph_has_a_skillogy_phase():
    """Each standard agent must map to a Skillogy phase, else it loses
    phase-scoped skill retrieval under ``DECEPTICON_USE_SKILLOGY``."""
    missing = sorted(set(STANDARD_GRAPHS) - set(_PHASE_FOR_ROLE))
    assert not missing, f"standard agents missing a _PHASE_FOR_ROLE mapping: {missing}"


_MOC_YAML = (
    Path(decepticon.__file__).resolve().parent / "skillogy" / "builder" / "seeds" / "moc.yaml"
)


def test_moc_parent_phases_are_seeded():
    """Every MoC ``parent_phase`` must be a seeded ``:Phase`` (moc.yaml contract)."""
    valid = _seeded_phase_names()
    data = yaml.safe_load(_MOC_YAML.read_text(encoding="utf-8"))
    bad = {
        moc["name"]: moc["parent_phase"] for moc in data["mocs"] if moc["parent_phase"] not in valid
    }
    assert not bad, f"MoC parent_phase not seeded in phases.yaml: {bad}"


# Domain phases an engagement is entered through by classifying a target asset.
# Excludes follow-on phases (post-exploit) and meta phases (orchestration /
# planning / analyst), which are not asset entry points. Update deliberately
# when a new asset-backed specialist domain is added.
_ASSET_ENTRY_DOMAINS = {
    "reconnaissance",
    "osint",
    "web-exploitation",
    "active-directory",
    "cloud",
    "mobile",
    "ics-ot",
    "iot",
    "wireless",
    "ai-security",
    "reverse-engineering",
    "dfir",
    "phishing",
    "smart-contracts",
    "supply-chain",
}


def test_asset_taxonomy_covers_entry_domains():
    """Every asset-entry domain must be reachable from some :AssetType via
    ENGAGED_VIA, or the classifier cannot route that target class to its agent."""
    reached = {phase for at in load_asset_types() for phase in at.phases}
    missing = sorted(_ASSET_ENTRY_DOMAINS - reached)
    assert not missing, f"asset entry domains unreachable from any AssetType: {missing}"
