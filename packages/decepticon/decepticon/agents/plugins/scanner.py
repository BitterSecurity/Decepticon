"""Scanner Agent — Stage 1 of the vulnresearch pipeline.

Broad-spectrum triage over large codebases (10^4 – 10^6 files). Runs on
the cheapest model tier available (Haiku) with a tight tool surface
focused on the sharded scanner helpers in
:mod:`decepticon.research.scanner_tools`.

The scanner deliberately has **no vulnerability-reasoning tools** — no
CVE lookup, no chain planner, no PoC validator. Its only job is to
produce ``CANDIDATE`` nodes for the Detector (Stage 2) to promote or
reject.

See ``decepticon/agents/prompts/scanner.md`` for the operating loop.

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

  1. **OSS default**: ``create_scanner_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["scanner"]``):

  SKILLS → FILESYSTEM → SANDBOX_NOTIFICATION → MODEL_FALLBACK
    → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (specialist, not an orchestrator).
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
from decepticon.tools.research.scanner_tools import SCANNER_TOOLS
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# kg_query / kg_stats were removed pending the Neo4j middleware redesign
# (see docs/design/neo4j-research-notes.md). KG surface is currently
# limited to the analyst agent. SCANNER_TOOLS is kept (it is the scanner
# plugin's purpose); note that kg_add_candidate inside SCANNER_TOOLS still
# routes through the broken graph_transaction shim and is in scope for
# the same refactor.
_STANDARD_TOOLS: dict[str, Any] = {
    # Tight tool surface: sharded scanner helpers + bash for directory
    # sizing only. NO vuln analysis tools.
    t.name: t
    for t in [*SCANNER_TOOLS, *BASH_TOOLS]
}


_SKILL_SOURCES: list[str] = [
    "/skills/plugins/scanner/",
    "/skills/standard/analyst/",
    "/skills/shared/",
]


_ROLE = "scanner"
_RECURSION_LIMIT = 2000  # issue #1145: raised from the per-role values (120-1000) — flash models loop in report phases; the env-tunable main budget is 2000 too


def create_scanner_agent(
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
    """Build the Scanner agent.

    Context engineering decisions:
      - Haiku-tier primary (see ``LLMModelMapping.scanner``) so 10^5-file
        sweeps cost pennies.
      - ``recursion_limit=60`` — scanner work is shallow; if it needs more
        iterations something is wrong (probably reading whole files).
      - Tools: sharded scanner helpers + ``kg_query`` + ``kg_stats``, plus
        ``bash`` for directory sizing (``du``, ``wc -l``, ``ls``). No other
        research tools.
      - Skills routed through ``/skills/plugins/scanner/`` + ``/skills/shared/``.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("scanner")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("scanner")``.
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
            override. Defaults to 60.

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
            skill_sources=_SKILL_SOURCES,
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
if is_bundle_enabled("plugins"):
    graph = create_scanner_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="scanner",
    description=(
        "Stage 1 — broad-spectrum scanner. Walks very large codebases "
        "in parallel shards and emits CANDIDATE nodes with heuristic "
        "suspicion scores. Use first on any new target. Cheap, fast, "
        "no vulnerability reasoning."
    ),
    factory=create_scanner_agent,
    parent_agents=("vulnresearch",),
    bundle="plugins",
    priority=10,
)
