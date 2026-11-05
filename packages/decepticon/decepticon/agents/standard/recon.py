"""Recon Agent — autonomous reconnaissance and intelligence gathering.

Specialist (non-orchestrator) bash-using agent. Standard middleware
stack with the bash-agent slots — EngagementContext, Skills,
Filesystem, SandboxNotification, ModelFallback, Summarization,
PromptCaching, PatchToolCalls. See
``decepticon.agents.middleware_slots.SLOTS_PER_ROLE`` for the canonical
role → slot mapping.

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

  1. **OSS default**: ``create_recon_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["recon"]``):

  ENGAGEMENT_CONTEXT → SKILLS → FILESYSTEM → SANDBOX_NOTIFICATION
    → MODEL_FALLBACK → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (standalone, not an orchestrator).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.middleware.step_budget import StepBudgetMiddleware, effective_recursion_limit
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.references.tools import killchain_lookup, oneliner_search
from decepticon.tools.web.search import web_fetch, web_search
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# Name-keyed registry so plugin overrides can target specific tools.
# KG tools were removed pending the Neo4j middleware redesign (see
# docs/design/neo4j-research-notes.md). Recon writes findings to files
# (recon/SUMMARY.md, findings/FIND-NNN.md); the future KG middleware
# will ingest those files into Neo4j without burdening the agent's
# tool surface.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        # References
        oneliner_search,
        killchain_lookup,
        # Open-web acquisition (ADR-0010): keyword search + RoE-gated fetch
        web_search,
        web_fetch,
        # Execution
        *BASH_TOOLS,
    ]
}


_ROLE = "recon"
_RECURSION_LIMIT = 1000


def create_recon_agent(
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
    """Build the Recon agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("recon")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("recon")``.
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
            override. Defaults to 1000.

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
    graph = create_recon_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="recon",
    description=(
        "Reconnaissance agent. Passive/active recon, OSINT, web/cloud recon. "
        "Use for: subdomain enumeration, port scanning, service detection, "
        "vulnerability scanning, OSINT gathering. "
        "Saves results under the active engagement workspace's recon/ directory."
    ),
    factory=create_recon_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=10,
)
