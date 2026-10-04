"""Soundwave Agent — engagement document writer.

Generates the eight planning documents (RoE / CONOPS / Deconfliction /
Threat Profile / Contact / Data Handling / Abort / Cleanup) that frame
the red team engagement. Does NOT generate OPPLAN — the orchestrator
owns OPPLAN directly via OPPLANMiddleware.

Named after the Decepticon intelligence officer who intercepts, processes,
and organizes strategic information for Megatron's operations.

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

  1. **OSS default**: ``create_soundwave_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

Middleware slots (per ``SLOTS_PER_ROLE["soundwave"]``):

  ENGAGEMENT_CONTEXT → SKILLS → FILESYSTEM → MODEL_FALLBACK
    → SUMMARIZATION → PROMPT_CACHING → PATCH_TOOL_CALLS

No SubAgent / OPPLAN (standalone, not an orchestrator).
No SandboxNotification (document-only, no bash tool).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.middleware.filesystem import FilesystemMiddleware
from decepticon.tools.interaction import ask_user_question, complete_engagement_planning
from decepticon_core.plugin_loader import is_bundle_enabled, load_plugin_callbacks

# Name-keyed registry of the standard tools. Exposed for library
# callers who want to splice into the default set (e.g.
# ``tools=[*_STANDARD_TOOLS.values(), my_extra_tool]``).
_STANDARD_TOOLS: dict[str, Any] = {
    "ask_user_question": ask_user_question,
    "complete_engagement_planning": complete_engagement_planning,
}

_ROLE = "soundwave"
_RECURSION_LIMIT = 200
_ALLOWED_TOOL_NAMES = frozenset(
    {
        "ask_user_question",
        "complete_engagement_planning",
        "load_skill",
        "find_skill",
        "traverse",
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
    }
)
_FILESYSTEM_TOOL_NAMES = frozenset({"ls", "read_file", "write_file", "edit_file", "glob", "grep"})


def _assert_planning_tools(tools: list[Any], middleware: list[Any]) -> None:
    if not any(
        isinstance(layer, FilesystemMiddleware) and layer._plan_only for layer in middleware
    ):
        raise ValueError("Soundwave requires a plan-scoped filesystem in Interview mode")
    for tool in [*tools, *(item for layer in middleware for item in getattr(layer, "tools", []))]:
        name = getattr(tool, "name", getattr(tool, "__name__", ""))
        if name not in _ALLOWED_TOOL_NAMES:
            raise ValueError(f"Soundwave cannot use {name or 'an unnamed tool'} in Interview mode")
    for tool in tools:
        if getattr(tool, "name", getattr(tool, "__name__", "")) in _FILESYSTEM_TOOL_NAMES:
            raise ValueError("Soundwave filesystem tools must use the plan-scoped middleware")
    for layer in middleware:
        if any(
            getattr(tool, "name", "") in _FILESYSTEM_TOOL_NAMES
            for tool in getattr(layer, "tools", [])
        ):
            if not isinstance(layer, FilesystemMiddleware) or not layer._plan_only:
                raise ValueError("Soundwave filesystem tools must use the plan-scoped middleware")


def create_soundwave_agent(
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
    """Build the Soundwave agent.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("soundwave")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("soundwave")``.
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
            override. Defaults to 200.

    Returns:
        Compiled LangGraph agent.
    """
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    if backend is None:
        backend = make_agent_backend(build_sandbox_backend())

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE)

    _assert_planning_tools(tools, middleware)

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


# Module-level graph for LangGraph Platform (langgraph serve).
if is_bundle_enabled("standard"):
    graph = create_soundwave_agent()
