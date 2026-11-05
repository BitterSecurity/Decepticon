"""Detector Agent — Stage 2 of the vulnresearch pipeline.

Given ``CANDIDATE`` nodes emitted by the Scanner, the Detector reads the
surrounding source via :class:`FilesystemMiddleware` (Read-only) and
decides whether each candidate is a real vulnerability worth promoting.

Key design choices — all enforced by the tool surface, not just the prompt:

- **No bash tool.** The Detector is pure code-reading + graph reasoning.
  Dropping bash prevents it from shelling out to semgrep/grep/etc., which
  both wastes tokens and pollutes its context.
- **No scanner tools.** Re-scanning is the Scanner's job; the Detector
  strictly consumes scanner output.
- **No ingesters.** The ``kg_ingest_*`` surface is for machine output
  (nmap, nuclei, sarif); the Detector emits hand-crafted vuln nodes.
- **No PoC runner.** Validation belongs to the Verifier stage.

Tools exposed: the core KG CRUD + query subset of ``RESEARCH_TOOLS``, plus
``cve_lookup`` / ``cve_by_package`` for dependency correlation. Nothing else.

EngagementContext and SandboxNotification slots are excluded (see
SLOTS_PER_ROLE — ``detector`` maps to ``_BASE_SLOTS`` only).

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

  1. **OSS default**: ``create_detector_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

No SandboxNotification (no bash tool). No SubAgent / OPPLAN (specialist).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.middleware.step_budget import StepBudgetMiddleware, effective_recursion_limit

# cve_lookup / cve_by_package are self-contained NVD/EPSS/KEV + OSV lookups (no
# KG/Neo4j dependency) — exactly the dependency-correlation surface this agent
# needs per its docstring. Only the kg_* tools in tools/research stay deferred
# pending the Neo4j middleware redesign.
from decepticon.tools.research.tools import cve_by_package, cve_lookup
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        cve_lookup,
        cve_by_package,
    ]
}

_SKILL_SOURCES: list[str] = [
    "/skills/plugins/detector/",
    "/skills/standard/analyst/",
    "/skills/shared/",
]


_ROLE = "detector"
_RECURSION_LIMIT = 2000  # issue #1145: raised from the per-role values (120-1000) — flash models loop in report phases; the env-tunable main budget is 2000 too


def create_detector_agent(
    *,
    # ── Dependencies (injected for testing / library composition) ────
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    # ── langchain-style composition (full replace when provided) ─────
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    # ── Tuning ───────────────────────────────────────────────────────
    recursion_limit: int | None = None,
):
    """Build the Detector agent.

    Notes:
      - The Detector reads source files via FilesystemMiddleware exclusively;
        no sandbox bash access (no sandbox= arg, no set_sandbox() call).
      - Skills are sourced from ``/skills/standard/analyst/*`` (shared with legacy
        analyst — each vuln class has its own playbook) plus a small
        detector-specific operating guide under ``/skills/plugins/detector/``.
      - ``recursion_limit=120`` — source review per candidate burns turns,
        but much less than full analyst iteration loops.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("detector")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("detector")``.
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
            override. Defaults to 120.

    Returns:
        Compiled LangGraph agent.
    """
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    # No set_sandbox() here — Detector intentionally has no bash tool.
    sandbox = build_sandbox_backend()

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
            sandbox=None,  # no SandboxNotification for detector
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
            "recursion_limit": effective_recursion_limit(recursion_limit or _RECURSION_LIMIT),
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform (langgraph serve)
if is_bundle_enabled("plugins"):
    graph = create_detector_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="detector",
    description=(
        "Stage 2 — vulnerability detector. Reads source around each "
        "CANDIDATE and promotes real bugs to VULNERABILITY + "
        "HYPOTHESIS nodes, or rejects them as false positives. "
        "Read-only (no bash)."
    ),
    factory=create_detector_agent,
    parent_agents=("vulnresearch",),
    bundle="plugins",
    priority=20,
)
