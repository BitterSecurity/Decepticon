"""Reverser Agent — binary analysis, firmware triage, exploit dev.

Specialises in taking an opaque binary (ELF, PE, Mach-O, firmware
image) and turning it into structured findings: dangerous imports,
embedded secrets, packer signatures, ROP gadget inventories, and
recon scripts for Ghidra / radare2 follow-up.

Tool surface (first-class research tools + bash):
    bin_identify, bin_strings, bin_packer, bin_rop,
    bin_symbols_report, bin_ghidra_script, bin_r2_script,
    + everything from the research.tools package.

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

  1. **OSS default**: ``create_reverser_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["reverser"]``):

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
from decepticon.tools.bash import BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon.tools.reversing.rea import REA_TOOLS, rea_configured
from decepticon.tools.reversing.tools import REVERSING_TOOLS
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# KG tools were removed pending the Neo4j middleware redesign (see
# docs/design/neo4j-research-notes.md). KG surface is currently limited
# to the analyst agent.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        # Reversing tools
        *REVERSING_TOOLS,
        # Execution
        *BASH_TOOLS,
    ]
}


_ROLE = "reverser"
_RECURSION_LIMIT = 1000


def create_reverser_agent(
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
    """Build the Reverser agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("reverser")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("reverser")``.
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
        standard_tools = dict(_STANDARD_TOOLS)
        if rea_configured():
            standard_tools.update({tool.name: tool for tool in REA_TOOLS})
        tools = build_tools(role=_ROLE, standard_tools=standard_tools)
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
        middleware=middleware,
        name=_ROLE,
    ).with_config(
        {
            "recursion_limit": recursion_limit or _RECURSION_LIMIT,
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


# Module-level graph for LangGraph Platform (langgraph serve)
if is_bundle_enabled("standard"):
    graph = create_reverser_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="reverser",
    description=(
        "Binary reversing specialist. Use for ELF/PE/Mach-O/firmware triage, "
        "packer detection, classified string extraction, symbol risk reports, "
        "ROP gadget inventories, and Radare2-assisted basic triage without the reversing workload. "
        "When the REA workload is available, use its Ghidra provider for decompilation, "
        "cross-references, P-code analysis, and source-bound Evidence. "
        "Ideal for thick clients, IoT firmware, game cheats, malware triage, "
        "and exploit dev hand-offs."
    ),
    factory=create_reverser_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=40,
)
