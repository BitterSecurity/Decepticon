"""Analyst Agent — vulnerability research specialist.

The Analyst is Decepticon's high-value discovery lane: source code review,
static analysis, CVE correlation, fuzzing, and exploit chain construction.
Unlike the recon/exploit agents which work primarily black-box, the
Analyst reads source, builds a persistent knowledge graph, and reasons
about multi-hop paths from entrypoints to crown jewels.

Tool surface (in addition to bash):
    REPORTING_TOOLS, REFERENCES_TOOLS, plus ``kg_record`` and
    ``kg_ingest`` supplied by the KG middleware slot
    (``SLOTS_PER_ROLE["analyst"]`` includes ``MiddlewareSlot.KG``;
    ``KGMiddleware`` constructs from ``DECEPTICON_NEO4J_*`` env at agent
    build time and exposes its two tools via ``self.tools``).

REPORTING_TOOLS currently routes through the legacy
``decepticon.tools.research._state`` shim; that retirement is PR-D
scope and tracked in
docs/design/2026-06-03-kg-middleware-redesign.md § 6.2.

Middleware stack — mirrors exploit.py. The analyst runs fewer shell
commands in favour of first-class research tools (semgrep, bandit,
gitleaks, KG operations via the middleware-supplied ``kg_record`` /
``kg_ingest``).

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

  1. **OSS default**: ``create_analyst_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["analyst"]``):

  ENGAGEMENT_CONTEXT → SKILLS → FILESYSTEM → KG → SANDBOX_NOTIFICATION
    → MODEL_FALLBACK → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (standalone, not an orchestrator). The KG slot
attaches ``KGMiddleware`` — owner of the Neo4j store and the agent-
facing ``kg_record`` / ``kg_ingest`` tools.
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
from decepticon.tools.references.tools import REFERENCES_TOOLS
from decepticon.tools.reporting.tools import REPORTING_TOOLS
from decepticon.tools.web.search import web_fetch, web_search
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    # KG surface arrives via the MiddlewareSlot.KG slot — KGMiddleware
    # exposes ``kg_record`` and ``kg_ingest`` through its ``self.tools``
    # attribute, merged into the agent's tool list at create_agent time.
    # REPORTING_TOOLS still routes through the legacy
    # ``decepticon.tools.research._state`` shim until PR-D extracts the
    # reporting tools; tracked in
    # docs/design/2026-06-03-kg-middleware-redesign.md § 6.2.
    t.name: t
    # Open-web acquisition (ADR-0010): advisory / CVE / write-up research for
    # the high-value discovery lane.
    for t in [web_search, web_fetch, *REPORTING_TOOLS, *REFERENCES_TOOLS, *BASH_TOOLS]
}


_ROLE = "analyst"
_RECURSION_LIMIT = 1000


def create_analyst_agent(
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
    """Build the Analyst agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("analyst")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("analyst")``.
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
    graph = create_analyst_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="analyst",
    description=(
        "Vulnerability research agent — the high-value discovery lane. "
        "Use for: source code review, static analysis (semgrep/bandit/gitleaks), "
        "dependency CVE sweeps, silent-patch diff hunting, fuzzing, taint "
        "analysis for SSRF/SQLi/IDOR/deserialization/prototype-pollution/"
        "command-injection/prompt-injection, and multi-hop exploit chain "
        "construction. Writes all observations into the KnowledgeGraph "
        "backend (default /workspace/kg.json, optional Neo4j) so "
        "findings survive across iterations."
    ),
    factory=create_analyst_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=30,
)
