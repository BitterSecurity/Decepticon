"""MobileOperator Agent - Android / iOS application attack lane.

Mobile skills already exist in the repo at
``packages/decepticon/decepticon/skills/standard/mobile/`` (android/
subdirectory + base SKILL.md) but no agent consumed them. This file
adds the missing dispatch surface so OPPLAN objectives tagged with
T1426 / T1556 / mobile-specific TTPs can route to a real specialist.

Tool surface (all via bash for the OSS bootstrap):

  - apktool / jadx-cli for Android static analysis.
  - frida + frida-server for Android dynamic instrumentation.
  - objection (frida wrapper) for one-liner hook scripts.
  - adb for device interaction (push, pull, install, shell, logcat).
  - class-dump / Hopper-CLI for iOS static analysis.
  - MobSF REST API for batch static+dynamic scans.

Hardware: the sandbox image needs an Android emulator (qemu-system-arm)
or a passthrough USB device for real-device work. The default OSS
sandbox does not ship the emulator (~3 GB); operators opt in via
COMPOSE_PROFILES=mobile (sandbox image rebuilt with INSTALL_MOBILE=true).
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
from decepticon.tools.references.tools import methodology_lookup, payload_search

# cve_lookup is a self-contained NVD/EPSS/CISA-KEV scorer (no KG/Neo4j
# dependency) — mobile targets ship native libs with version-specific CVEs, so
# keep it on the surface. Only the kg_* tools in tools/research stay deferred
# pending the Neo4j middleware redesign.
from decepticon.tools.research.tools import cve_lookup
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        payload_search,
        methodology_lookup,
        cve_lookup,
        *BASH_TOOLS,
    ]
}


_ROLE = "mobile_operator"
_RECURSION_LIMIT = 250
_SKILL_SOURCES: list[str] = ["/skills/standard/mobile/", "/skills/shared/"]


def create_mobile_operator_agent(
    *,
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    sandbox: Any = None,
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    recursion_limit: int | None = None,
):
    """Build the MobileOperator agent."""
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
    graph = (
        create_mobile_operator_agent()
    )  # lgtm[py/unused-global-variable]  # consumed by langgraph at runtime


SUBAGENT_SPEC = SubAgentSpec(
    name="mobile_operator",
    description=(
        "Android / iOS application attack specialist. Use when the "
        "engagement scope includes mobile apps: static analysis "
        "(apktool/jadx/class-dump), dynamic instrumentation "
        "(frida/objection), SSL pinning + root/jailbreak detection "
        "bypass, exported-component abuse, WebView JavaScript bridge "
        "exploitation, and MobSF integration. Existing skill tree at "
        "skills/standard/mobile/."
    ),
    factory=create_mobile_operator_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=55,
)
