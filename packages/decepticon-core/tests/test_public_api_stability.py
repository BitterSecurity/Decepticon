"""Stability snapshot — every public ``decepticon-core`` name imports.

Locks in the public surface so accidental removal / rename fails CI.
Spec §6.1 enumerates the SemVer-stable surface plugin authors and
downstream consumers can rely on at v1.0; this test asserts each
listed name is importable from its declared module.

To intentionally remove or rename a public name:
  1. Update this manifest in the same commit
  2. Update the CHANGELOG (and migration guide if the rename breaks
     callers)
  3. Bump the major version if at 1.0+
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

# (module, name) pairs. Every entry must resolve to an importable
# attribute. Reorganize by category for readability.
CORE_PUBLIC_API: tuple[tuple[str, str], ...] = (
    # decepticon_core.types.engagement
    ("decepticon_core.types.engagement", "RoE"),
    ("decepticon_core.types.engagement", "OPPLAN"),
    ("decepticon_core.types.engagement", "Finding"),
    ("decepticon_core.types.engagement", "Evidence"),
    ("decepticon_core.types.engagement", "Objective"),
    ("decepticon_core.types.engagement", "AttackPath"),
    ("decepticon_core.types.engagement", "ObjectivePhase"),
    ("decepticon_core.types.engagement", "ObjectiveStatus"),
    ("decepticon_core.types.engagement", "OpsecLevel"),
    ("decepticon_core.types.engagement", "C2Tier"),
    ("decepticon_core.types.engagement", "FindingSeverity"),
    ("decepticon_core.types.engagement", "FindingConfidence"),
    ("decepticon_core.types.engagement", "RemediationPriority"),
    ("decepticon_core.types.engagement", "EngagementType"),
    ("decepticon_core.types.engagement", "ScopeEntry"),
    ("decepticon_core.types.engagement", "EscalationContact"),
    # decepticon_core.types.engagement — OPPLAN objective lifecycle model
    ("decepticon_core.types.engagement", "OBJECTIVE_STATUS_TRANSITIONS"),
    ("decepticon_core.types.engagement", "OBJECTIVE_INVALIDATION_TRANSITIONS"),
    ("decepticon_core.types.engagement", "RESOLVED_OBJECTIVE_STATUSES"),
    ("decepticon_core.types.engagement", "ACTIONABLE_OBJECTIVE_STATUSES"),
    ("decepticon_core.types.engagement", "PREREQUISITE_BOUND_OBJECTIVE_STATUSES"),
    ("decepticon_core.types.engagement", "can_transition_objective"),
    ("decepticon_core.types.engagement", "next_objective_statuses"),
    ("decepticon_core.types.engagement", "ObjectiveOutcome"),
    # decepticon_core.types.llm
    ("decepticon_core.types.llm", "Tier"),
    ("decepticon_core.types.llm", "AuthMethod"),
    ("decepticon_core.types.llm", "ModelProfile"),
    ("decepticon_core.types.llm", "Credentials"),
    ("decepticon_core.types.llm", "ProxyConfig"),
    ("decepticon_core.types.llm", "ModelAssignment"),
    ("decepticon_core.types.llm", "LLMModelMapping"),
    # decepticon_core.types.kg
    ("decepticon_core.types.kg", "Node"),
    ("decepticon_core.types.kg", "Edge"),
    ("decepticon_core.types.kg", "NodeKind"),
    ("decepticon_core.types.kg", "EdgeKind"),
    ("decepticon_core.types.kg", "Severity"),
    ("decepticon_core.types.kg", "KnowledgeGraph"),
    # decepticon_core.types.roe — machine-readable RoE enforcement schema
    ("decepticon_core.types.roe", "ScopeRule"),
    ("decepticon_core.types.roe", "MachineEnforcement"),
    ("decepticon_core.types.roe", "Decision"),
    ("decepticon_core.types.roe", "evaluate_target"),
    ("decepticon_core.types.roe", "evaluate_command"),
    # decepticon_core.types.ocsf_event — AutoBot normalized event envelope
    ("decepticon_core.types.ocsf_event", "OcsfEvent"),
    ("decepticon_core.types.ocsf_event", "SignalType"),
    ("decepticon_core.types.ocsf_event", "SeverityId"),
    ("decepticon_core.types.ocsf_event", "Observable"),
    ("decepticon_core.types.ocsf_event", "Metadata"),
    ("decepticon_core.types.ocsf_event", "PROMOTED_FIELDS"),
    # decepticon_core.types.event_store — event store query contract value types
    ("decepticon_core.types.event_store", "EventPage"),
    ("decepticon_core.types.event_store", "SourceInfo"),
    ("decepticon_core.types.event_store", "StoreMetrics"),
    ("decepticon_core.types.event_store", "IngestResult"),
    ("decepticon_core.types.event_store", "SEARCHABLE_FIELDS"),
    # decepticon_core.types.host — per-run configuration a trusted host passes
    ("decepticon_core.types.host", "HostRunConfigurable"),
    ("decepticon_core.types.host", "HOST_RUN_CONFIGURABLE_KEYS"),
    # decepticon_core.protocols
    ("decepticon_core.protocols", "BackendProtocol"),
    ("decepticon_core.protocols", "MiddlewareProtocol"),
    ("decepticon_core.protocols", "ToolProtocol"),
    ("decepticon_core.protocols", "CallbackProtocol"),
    ("decepticon_core.protocols", "LLMProtocol"),
    ("decepticon_core.protocols", "SandboxProtocol"),
    ("decepticon_core.protocols", "AgentProtocol"),
    ("decepticon_core.protocols", "EventStoreProtocol"),
    ("decepticon_core.protocols", "CoverageAdapterProtocol"),
    # decepticon_core.contracts.slots
    ("decepticon_core.contracts.slots", "MiddlewareSlot"),
    ("decepticon_core.contracts.slots", "SAFETY_CRITICAL_SLOTS"),
    ("decepticon_core.contracts.slots", "SLOTS_PER_ROLE"),
    # decepticon_core.contracts.contributions
    ("decepticon_core.contracts.contributions", "ToolContribution"),
    ("decepticon_core.contracts.contributions", "MiddlewareContribution"),
    ("decepticon_core.contracts.contributions", "PromptContribution"),
    ("decepticon_core.contracts.contributions", "SubAgentContribution"),
    ("decepticon_core.contracts.contributions", "SafetyDeclaration"),
    # decepticon_core.registry
    ("decepticon_core.registry", "PluginRegistry"),
    ("decepticon_core.registry", "PluginInfo"),
    ("decepticon_core.registry", "PluginConflictWarning"),
    ("decepticon_core.registry", "RoleRegistry"),
    ("decepticon_core.registry", "RoleSpec"),
    ("decepticon_core.registry", "RoleResolution"),
    ("decepticon_core.registry", "MiddlewareInfo"),
    ("decepticon_core.registry", "ToolInfo"),
    ("decepticon_core.registry", "OverrideInfo"),
    ("decepticon_core.registry", "SafetyRegistry"),
    ("decepticon_core.registry", "SkillSourceRegistry"),
    # decepticon_core.plugin_loader (still here for back-compat;
    # contracts will eventually split into contracts/, registry/ per
    # spec §9.1 but the public symbol set stays the same).
    ("decepticon_core.plugin_loader", "PluginBundle"),
    ("decepticon_core.plugin_loader", "SubAgentSpec"),
    ("decepticon_core.plugin_loader", "is_bundle_enabled"),
    ("decepticon_core.plugin_loader", "load_plugin_tools"),
    ("decepticon_core.plugin_loader", "load_plugin_middleware"),
    ("decepticon_core.plugin_loader", "load_plugin_callbacks"),
    ("decepticon_core.plugin_loader", "load_plugin_skill_sources"),
    ("decepticon_core.plugin_loader", "load_subagents_for_parent"),
    ("decepticon_core.plugin_loader", "load_plugin_agents"),
    # decepticon_core.utils
    ("decepticon_core.utils.config", "DecepticonConfig"),
    ("decepticon_core.utils.config", "LLMConfig"),
    ("decepticon_core.utils.config", "load_config"),
    ("decepticon_core.utils.logging", "configure_logging"),
    ("decepticon_core.utils.logging", "get_logger"),
)


@pytest.mark.parametrize(("module_name", "attr"), CORE_PUBLIC_API)
def test_public_name_importable(module_name: str, attr: str) -> None:
    """Each (module, name) in the manifest must resolve.

    Failures indicate accidental removal or rename — fix the
    implementation, or update both this manifest and the CHANGELOG
    in the same commit.
    """
    module = importlib.import_module(module_name)
    value: Any = getattr(module, attr, None)
    assert value is not None, (
        f"{module_name}.{attr} returned None — accidental removal? "
        f"If intentional, update this manifest + CHANGELOG together."
    )


def test_manifest_count_unchanged() -> None:
    """Snapshot the manifest size so silent drift surfaces.

    Bumping the count is fine in either direction — the test
    documents the intentional change in the diff. Lowering without
    a major-version bump is an audit signal.
    """
    # Update this number deliberately when adding/removing public names.
    # 2026-10-22: 75 -> 74, EnforcementMode removed (legacy audit/warn/enforce
    # modes retired; RoE now always fail-closed enforce). See CHANGELOG.
    # 2026-10-23: 74 -> 80, AutoBot OcsfEvent envelope added (OcsfEvent,
    # SignalType, SeverityId, Observable, Metadata, PROMOTED_FIELDS). See CHANGELOG.
    # 2026-10-23: 80 -> 86, AutoBot event-store query contract added
    # (EventStoreProtocol + EventPage, SourceInfo, StoreMetrics, IngestResult,
    # SEARCHABLE_FIELDS). See CHANGELOG.
    # 2026-10-23: 86 -> 87, AutoBot CoverageAdapterProtocol added. See CHANGELOG.
    expected = 97
    actual = len(CORE_PUBLIC_API)
    assert actual == expected, (
        f"CORE_PUBLIC_API has {actual} entries, expected {expected}. "
        f"If intentional, bump this number AND update the CHANGELOG."
    )
