"""Vulnresearch Orchestrator — five-stage modular vulnerability pipeline.

Mirrors :mod:`decepticon.agents.decepticon` (the red-team orchestrator)
but swaps the sub-agent roster for the five vulnresearch specialists:
scanner → detector → verifier → patcher → exploiter. State passes
between stages exclusively through the KnowledgeGraph backend (default
``/workspace/kg.json``; optional Neo4j), so every sub-agent runs with
fresh context and only reads the slice of graph state that matters for
its work item.

Design notes:
  - Uses ``create_agent()`` directly with an explicit middleware stack
    so the OPPLAN tracker, SubAgent dispatcher, and skills loader are
    all composed deterministically.
  - Sub-agents are wrapped in :class:`StreamingRunnable` so their tool
    calls and messages stream through both the Python CLI and the
    LangGraph Platform HTTP API.
  - The orchestrator itself has only ``kg_query``/``kg_stats`` as tools
    (plus the SubAgent ``task()`` and OPPLAN CRUD). It MUST NOT touch
    bash, source files, or PoCs directly.
  - EngagementContext slot is NOT included (see SLOTS_PER_ROLE).

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

  1. **OSS default**: ``create_vulnresearch_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

NOTE: set_sandbox() is intentionally NOT called here — the orchestrator
must not run bash. Each sub-agent that needs bash calls set_sandbox()
from its own factory.
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents import create_agent

from decepticon.agents._benchmark_mode import benchmark_skill_sources
from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.core.subagent_streaming import StreamingRunnable
from decepticon.llm import LLMFactory
from decepticon.middleware.step_budget import StepBudgetMiddleware
from decepticon_core.plugin_loader import (
    is_bundle_enabled,
    load_plugin_callbacks,
    load_subagents_for_parent,
)

_ROLE = "vulnresearch"
_RECURSION_LIMIT = 2000  # issue #1145: raised from the per-role values (120-1000) — flash models loop in report phases; the env-tunable main budget is 2000 too

# kg_query / kg_stats were removed pending the Neo4j middleware redesign
# (see docs/design/neo4j-research-notes.md). KG surface is currently
# limited to the analyst agent. This orchestrator now relies purely on
# subagent dispatch and has no direct tools.
_STANDARD_TOOLS: dict[str, Any] = {}


logger = logging.getLogger(__name__)


def create_vulnresearch_agent(
    *,
    # ── Dependencies (injected for testing / library composition) ────
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    subagents: list | None = None,
    # ── langchain-style composition (full replace when provided) ─────
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    # ── Tuning ───────────────────────────────────────────────────────
    recursion_limit: int | None = None,
):
    """Build the Vulnresearch orchestrator.

    Tool surface is intentionally tiny: ``kg_query`` + ``kg_stats`` for
    graph inspection, plus the OPPLAN CRUD tools (injected by
    :class:`OPPLANMiddleware`) and the ``task()`` dispatcher (injected
    by :class:`SubAgentMiddleware`). Everything else is delegated.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("vulnresearch")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("vulnresearch")``.
        subagents: explicit sub-agent list. When ``None`` (default),
            sub-agents are discovered via ``load_subagents_for_parent``
            and each wrapped in ``StreamingRunnable``.
        tools: full tool list — when provided, replaces the standard
            registry entirely. When ``None`` (default), the OSS
            baseline (``kg_query`` + ``kg_stats``) is built and plugin
            overrides applied.
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

    sandbox = build_sandbox_backend()

    if backend is None:
        backend = make_agent_backend(sandbox)

    # Build sub-agents via plugin-loader discovery. Each subagent
    # declares itself as a ``SUBAGENT_SPEC`` module constant registered
    # under the ``decepticon.subagents`` entry-point group; this main
    # agent picks up every spec whose ``parent_agents`` includes
    # ``"vulnresearch"``. Community or downstream plugin packages can extend
    # this roster without modifying OSS — see
    # ``decepticon/plugin_loader.py`` for the loader contract.
    if subagents is None:
        # Optional subagent packages (e.g. the AutoBot defender) degrade
        # gracefully: a subagent whose factory raises (missing model env,
        # absent optional dep) is skipped with a warning — never the parent
        # agent. The purple loop activates when the package is configured.
        subagents = []
        for spec in load_subagents_for_parent(_ROLE):
            try:
                subagents.append(
                    CompiledSubAgent(
                        name=spec.name,
                        description=spec.description,
                        runnable=StreamingRunnable(spec.factory(), spec.name),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - optional capability
                logger.warning(
                    "subagent %r skipped (optional capability unavailable): %s",
                    spec.name,
                    exc,
                )

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        skill_sources = [
            "/skills/plugins/vulnresearch/",
            "/skills/shared/",
            *benchmark_skill_sources(),
        ]
        middleware = build_middleware(
            role=_ROLE,
            skill_sources=skill_sources,
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=None,  # orchestrator has no bash tool / sandbox notification
            subagents=subagents,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE, shared=[])

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
            "recursion_limit": recursion_limit or _RECURSION_LIMIT,
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform.
#
# Construction is guarded by ``is_bundle_enabled("plugins")``: when the
# bundle is disabled (the OSS default) the subagent roster is empty,
# which would otherwise cause ``SubAgentMiddleware`` to raise at
# module-import time. Skipping construction keeps ``import
# decepticon.agents.plugins.vulnresearch`` side-effect-free for default
# installs; opt-in via ``DECEPTICON_PLUGINS=standard,plugins`` (or the
# equivalent config-file entry) flips this on.
if is_bundle_enabled("plugins"):
    graph = create_vulnresearch_agent()
