"""Decepticon — autonomous red team coordinator agent.

Engagement-ready agent that builds the OPPLAN from an approved RoE and optional
Plan documents, then executes the kill chain by delegating to specialist
sub-agents. The launcher selects this assistant for Red mode, including fresh
direct-scope engagements. Soundwave is selected only for optional Plan mode.

Middleware stack (selected for orchestration):
  1. EngagementContextMiddleware — inject engagement metadata (slug, target, RoE)
  2. SkillsMiddleware — progressive disclosure of SKILL.md knowledge
  3. FilesystemMiddleware — file ops for reading/updating engagement docs
  4. SubAgentMiddleware — task() tool for delegating to sub-agents
  5. OPPLANMiddleware — OPPLAN CRUD tools (create/add/get/list/update objectives)
  6. ModelOverrideMiddleware — runtime /model switch support
  7. ModelFallbackMiddleware — primary → fallback on provider failure
  8. SummarizationMiddleware — auto-compact for long orchestration sessions
  9. AnthropicPromptCachingMiddleware — cache system prompt for Anthropic models
  10. PatchToolCallsMiddleware — repair dangling tool calls

The orchestrator has tools=[] — all offensive work goes through task()
delegation to specialist sub-agents. SandboxNotificationMiddleware lives
on each sub-agent (where bash actually runs), not here.

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

  1. **OSS default**: ``create_decepticon_agent()`` — no args.
  2. **Plugin override** (Docker / pip-installed plugin): authors ship
     ``PluginBundle(...)`` under ``decepticon.bundles``; the factory
     discovers and applies it automatically.
  3. **Full custom** (library composer): import building blocks from
     ``decepticon.middleware`` / ``decepticon.tools`` and compose with
     ``langchain.agents.create_agent`` directly. Decepticon's factory
     is bypassed entirely.

SubAgent / OPPLAN (orchestrator — delegates all work via task()).
No SandboxNotification (bash lives on sub-agents). Soundwave is NOT a
sub-agent: the launcher routes to its standalone assistant in Plan mode.
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent
from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.core.subagent_streaming import StreamingRunnable
from decepticon.llm import LLMFactory
from decepticon_core.plugin_loader import (
    is_bundle_enabled,
    load_plugin_callbacks,
    load_subagents_for_parent,
)

_ROLE = "decepticon"
_RECURSION_LIMIT = 400


def create_decepticon_agent(
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
    """Build the Decepticon orchestrator.

    Args:
        backend: deepagents-style filesystem backend. Defaults to
            ``make_agent_backend(build_sandbox_backend())``.
        llm: bound chat model. Defaults to
            ``LLMFactory().get_model("decepticon")``.
        fallback_models: passed to ``ModelFallbackMiddleware``. Defaults
            to ``LLMFactory().get_fallback_models("decepticon")``.
        subagents: explicit sub-agent list. When ``None`` (default),
            sub-agents are discovered via ``load_subagents_for_parent``
            and each wrapped in ``StreamingRunnable``.
        tools: full tool list — when provided, replaces the standard
            registry entirely. When ``None`` (default), the OSS
            baseline (``{}``) is built and plugin overrides applied.
            The orchestrator delegates all work; tools=[] by design.
        middleware: full middleware list — when provided, replaces the
            OSS slot stack entirely. When ``None``, the baseline is
            assembled with plugin slot overrides applied.
        system_prompt: full prompt — when provided, replaces the
            baseline. When ``None``, the standard prompt is loaded and
            plugin prompt overrides are applied.
        recursion_limit: ``with_config({"recursion_limit": ...})``
            override. Defaults to 400.

    Returns:
        Compiled LangGraph agent.
    """
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    # Filesystem backend for the orchestrator. HTTP transport is the single
    # supported path (see backends/factory.py).
    sandbox = build_sandbox_backend()

    if backend is None:
        backend = make_agent_backend(sandbox)

    # Build sub-agents via plugin-loader discovery. Each subagent declares
    # itself as a ``SUBAGENT_SPEC`` module constant registered under the
    # ``decepticon.subagents`` entry-point group; this main agent picks
    # up every spec whose ``parent_agents`` includes ``"decepticon"``.
    # Community or downstream plugin packages can extend this roster without
    # modifying OSS — see ``decepticon/plugin_loader.py`` for the loader
    # contract and ``pyproject.toml`` for the registered specs.
    #
    # Each discovered subagent is wrapped in StreamingRunnable so its
    # tool calls, results, and AI messages stream through both Python CLI
    # (UIRenderer) and LangGraph Platform HTTP API (get_stream_writer →
    # custom events).
    if subagents is None:
        subagents = [
            CompiledSubAgent(
                name=spec.name,
                description=spec.description,
                runnable=StreamingRunnable(spec.factory(), spec.name),
            )
            for spec in load_subagents_for_parent(_ROLE)
        ]

    if tools is None:
        # The orchestrator's domain tools are ``{}`` — all offensive
        # work goes through ``task()`` to a specialist. The exception
        # is ADR-0006 ``ops_*``: only the orchestrator may bring
        # specialist workloads up or down (least-privilege per §2,
        # so a compromised sub-agent cannot spin up unrelated
        # infrastructure). Sub-agents see neither these tools nor the
        # underlying daemon socket.
        #
        # Register ``ops_*`` ONLY when the opscontrol daemon socket is
        # present (``decepticon start`` topology). Daemon-less topologies —
        # ``make dev`` / ``make smoke``, and hosted deployments that manage
        # workload teardown externally — have no socket, so offering these
        # tools would only let the orchestrator call something that returns
        # ``opscontrol_unreachable``. Gating keeps the toolset honest per
        # topology instead of relying on the model to avoid an unusable tool.
        from decepticon.tools.ops import OPS_TOOLS, ops_available

        standard_tools = {t.name: t for t in OPS_TOOLS} if ops_available() else {}
        tools = build_tools(role=_ROLE, standard_tools=standard_tools)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=None,  # orchestrator has no bash tool / sandbox notification
            subagents=subagents,
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE)

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


# Module-level graph for LangGraph Platform.
#
# Construction is guarded by ``is_bundle_enabled("standard")`` for
# symmetry with the plugins bundle's main agent. The OSS default
# (``DECEPTICON_PLUGINS`` unset or set to ``standard``) keeps this on;
# if a user explicitly disables standard (e.g. ``DECEPTICON_PLUGINS=plugins``)
# the graph is skipped to avoid empty-subagent crashes.
if is_bundle_enabled("standard"):
    graph = create_decepticon_agent()
