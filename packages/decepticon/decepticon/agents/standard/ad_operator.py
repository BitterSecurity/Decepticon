"""AD Operator Agent — Active Directory and Windows attack lane.

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

  1. **OSS default**: ``create_ad_operator_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["ad_operator"]``):

  ENGAGEMENT_CONTEXT → SKILLS → FILESYSTEM → KG → SANDBOX_NOTIFICATION
    → MODEL_FALLBACK → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (standalone, not an orchestrator). The KG slot
exposes ``kg_record`` / ``kg_ingest`` so the operator can persist
BloodHound-derived principals, paths, and confirmed AD findings into
the engagement graph alongside (not in place of) the legacy AD_TOOLS
ingest path.
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
from decepticon.tools.ad.tools import AD_TOOLS
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.references.tools import killchain_lookup
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# Generic KG tools and the chain planner were removed pending the Neo4j
# middleware redesign (see docs/design/neo4j-research-notes.md). The
# AD_TOOLS surface (BloodHound ingestion, DCSync / ADCS / delegation /
# GPO / shadow-creds audits, Kerberos hash classification) is kept
# because BloodHound integration is the AD operator's primary lane.
# Note: AD_TOOLS themselves still go through the broken graph_transaction
# shim internally — their migration to KGStore needs a dedicated
# BloodHound-schema RFC and is NOT part of the slot adoption here.
# What the KG slot adds is the explicit ``kg_record`` / ``kg_ingest``
# tools (injected by KGMiddleware) so the operator can write confirmed
# findings, credentials, and manually-traced paths to the engagement
# graph alongside whatever AD_TOOLS legacy ingest writes.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        # AD tools
        *AD_TOOLS,
        # References
        killchain_lookup,
        # Execution
        *BASH_TOOLS,
    ]
}


_ROLE = "ad_operator"
_RECURSION_LIMIT = 1000
_SKILL_SOURCES: list[str] = ["/skills/standard/ad/", "/skills/shared/"]


def create_ad_operator_agent(
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
    """Build the ADOperator agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("ad_operator")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("ad_operator")``.
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
    graph = create_ad_operator_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="ad_operator",
    description=(
        "Active Directory / Windows attack specialist. Use after initial "
        "internal foothold: BloodHound ingestion, Kerberoast / AS-REP roast, "
        "ADCS ESC1-ESC15 scanning, DCSync candidate detection, and multi-hop "
        "AD attack path planning. Complements postexploit for Windows "
        "engagements."
    ),
    factory=create_ad_operator_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=70,
)
