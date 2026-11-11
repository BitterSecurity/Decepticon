"""Pure-pydantic types for the Decepticon contract layer.

Four submodules:

  * ``engagement`` — red-team planning documents (RoE, ConOps, OPPLAN,
    Finding, Objective, OpsecLevel, C2Tier, MITREPhase, ...). Was
    ``decepticon/core/schemas.py``.
  * ``llm`` — model selection types (Tier, AuthMethod, ModelProfile,
    ModelAssignment, ModelRouter, Credentials, LLMModelMapping,
    ProxyConfig). Was ``decepticon/llm/models.py``.
  * ``kg`` — knowledge-graph node / edge types and helpers. Was
    ``decepticon/tools/research/graph.py``.
  * ``roe`` — machine-readable Rules-of-Engagement *enforcement* schema
    (``ScopeRule``, ``MachineEnforcement``,
    ``Decision``, ``evaluate_target``, ``evaluate_command``). The
    evaluation layer that decides allow/deny for a target or command;
    consumed by the framework's RoE-enforcement middleware. Distinct from
    ``engagement.RoE`` (the planning document).
  * ``ocsf_event`` — AutoBot's OCSF-aligned normalized event envelope
    (``OcsfEvent``, ``SignalType``, ``SeverityId``, ``Observable``,
    ``Metadata``, ``PROMOTED_FIELDS``). The single contract every coverage
    adapter normalizes to; read source-agnostically by detection and the
    Blue agent. See ``docs/design/2026-10-23-autobot-redesign.md``.
  * ``event_store`` — value types of the swappable event-store query contract
    (``EventPage``, ``SourceInfo``, ``StoreMetrics``, ``IngestResult``,
    ``SEARCHABLE_FIELDS``). Callable surface is
    ``protocols.event_store.EventStoreProtocol``.
  * ``host`` — the per-run ``config["configurable"]`` keys a trusted host
    passes to the framework (``HostRunConfigurable``). Callable surface is
    ``decepticon_sdk.host``.

These modules import only ``pydantic`` + stdlib + ``typing_extensions`` —
no ``langchain`` / ``langgraph`` / ``deepagents`` / ``httpx`` /
``fastapi``. Suitable to import from any context.
"""

from __future__ import annotations

from decepticon_core.types import engagement, event_store, host, kg, llm, ocsf_event, roe

__all__ = ["engagement", "llm", "kg", "ocsf_event", "event_store", "roe", "host"]
