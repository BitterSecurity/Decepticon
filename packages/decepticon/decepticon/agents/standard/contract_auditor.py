"""Contract Auditor Agent — Solidity / EVM smart contract audit.

Walks a Foundry / Hardhat repo, runs the offline pattern scanner,
ingests Slither JSON, generates PoC tests via Foundry templates, and
promotes confirmed findings into the KnowledgeGraph for chain
reasoning (e.g. oracle manipulation → flash loan → drain chain).

Library API
-----------
Factory shape mirrors ``langchain.agents.create_agent`` /
``deepagents.create_deep_agent`` — every keyword is optional, and
explicit values fully replace the OSS baseline:

  - ``tools=[...]``         full tool list (overrides the standard set)
  - ``middleware=[...]``    full middleware list (overrides the slot stack)
  - ``system_prompt="..."`` full prompt (overrides the loaded baseline)

When a keyword is ``None`` (default), the factory builds the OSS
baseline AND applies any plugin overrides discovered via the
``decepticon.bundles`` entry-point group. Three usage paths converge
cleanly:

  1. **OSS default**: ``create_contract_auditor_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["contract_auditor"]``):

  ENGAGEMENT_CONTEXT → SKILLS → FILESYSTEM → KG → SANDBOX_NOTIFICATION
    → MODEL_FALLBACK → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (standalone, not an orchestrator). The KG slot
exposes ``kg_record`` / ``kg_ingest`` so confirmed Foundry-validated
findings and chain candidates land in the engagement graph alongside
(not in place of) the legacy CONTRACT_TOOLS slither ingest path.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents._benchmark_mode import benchmark_skill_sources
from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.middleware.step_budget import StepBudgetMiddleware, effective_recursion_limit
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.contracts.tools import CONTRACT_TOOLS
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# Generic KG tools, kg_ingest_sarif, cve_lookup, and report_hackerone
# were removed pending the Neo4j middleware redesign (see
# docs/design/neo4j-research-notes.md). The CONTRACT_TOOLS surface
# (Solidity pattern scanner, Slither ingest, Foundry PoC test
# generators) is kept because it is the contract auditor's primary
# lane. Note: slither_ingest inside CONTRACT_TOOLS still routes through
# the broken graph_transaction shim — its migration to KGStore is a
# follow-up RFC and is NOT covered by the slot adoption here. What the
# KG slot adds is the explicit ``kg_record`` / ``kg_ingest`` tools
# (injected by KGMiddleware) so Foundry-confirmed findings land in the
# engagement graph as structured Finding nodes.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        # Contract tools
        *CONTRACT_TOOLS,
        # Execution
        *BASH_TOOLS,
    ]
}


_ROLE = "contract_auditor"
_RECURSION_LIMIT = 1000
_SKILL_SOURCES: list[str] = ["/skills/standard/contracts/", "/skills/shared/"]


def create_contract_auditor_agent(
    *,
    # ── Dependencies (injected for testing / library composition) ────
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    sandbox: Any = None,
    # ── langchain-style composition (full replace when provided) ─────
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    # ── Tuning ───────────────────────────────────────────────────────
    recursion_limit: int | None = None,
):
    """Build the ContractAuditor agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("contract_auditor")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("contract_auditor")``.
        sandbox: sandbox backend for bash execution and
            ``SandboxNotificationMiddleware``. Defaults to
            ``build_sandbox_backend()``.
        tools: full tool list — when provided, replaces the standard
            registry entirely. When ``None`` (default), the OSS
            baseline is built and plugin overrides (via
            ``decepticon.bundles``) are applied.
        middleware: full middleware list — when provided, replaces the
            OSS slot stack entirely. When ``None``, the baseline is
            assembled with plugin slot overrides applied.
        system_prompt: full prompt — when provided, replaces the
            baseline. When ``None``, the standard prompt is loaded and
            plugin prompt overrides are applied.
        recursion_limit: ``with_config({"recursion_limit": ...})``
            override. Defaults to 250.

    Returns:
        Compiled LangGraph agent.
    """
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    if sandbox is None:
        sandbox = build_sandbox_backend()
    set_sandbox(sandbox)

    if backend is None:
        backend = make_agent_backend(sandbox)

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            skill_sources=[*_SKILL_SOURCES, *benchmark_skill_sources()],
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=sandbox,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE, shared=["bash"])

    return create_agent(
        llm,
        system_prompt=system_prompt,
        tools=tools,
        middleware=[
            *middleware,
            StepBudgetMiddleware(total_steps=recursion_limit or _RECURSION_LIMIT),
        ],
        name=_ROLE,
    ).with_config(
        {
            "recursion_limit": effective_recursion_limit(recursion_limit or _RECURSION_LIMIT),
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform (langgraph serve)
if is_bundle_enabled("standard"):
    graph = create_contract_auditor_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="contract_auditor",
    description=(
        "Solidity / EVM smart contract audit specialist. Use for DeFi / "
        "smart-contract engagements: reentrancy, oracle manipulation, flash "
        "loan abuse, access control gaps, upgradeable proxies, signature "
        "replay. Runs Slither ingestion, solidity pattern scanner, and "
        "Foundry PoC test harness generation."
    ),
    factory=create_contract_auditor_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=50,
)
