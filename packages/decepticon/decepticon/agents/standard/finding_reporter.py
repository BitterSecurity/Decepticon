"""Report one adjudicated finding in its canonical workspace file."""

from __future__ import annotations

from typing import Any

from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled

_ROLE = "finding_reporter"
_SKILL_SOURCES = [
    "/skills/standard/finding_reporter/",
    "/skills/shared/",
]


def create_finding_reporter(
    *,
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    sandbox: Any = None,
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    recursion_limit: int = 150,
):
    """Build a filesystem-only reporting agent with deterministic CVSS scoring."""
    from langchain.agents import create_agent

    from decepticon.agents.build import build_middleware, build_tools
    from decepticon.agents.prompts import load_prompt
    from decepticon.backends import build_sandbox_backend, make_agent_backend
    from decepticon.llm import LLMFactory
    from decepticon.tools.reporting.cvss import cvss_score_tool
    from decepticon.tools.reporting.finding_quality import build_finding_quality_tool
    from decepticon_core.plugin_loader import load_plugin_callbacks

    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)
    if sandbox is None:
        sandbox = build_sandbox_backend()
    if backend is None:
        backend = make_agent_backend(sandbox)
    if tools is None:
        quality_tool = build_finding_quality_tool(backend)
        tools = build_tools(
            role=_ROLE,
            standard_tools={cvss_score_tool.name: cvss_score_tool, quality_tool.name: quality_tool},
        )
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=sandbox,
            skill_sources=_SKILL_SOURCES,
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
            "recursion_limit": recursion_limit,
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


SUBAGENT_SPEC = SubAgentSpec(
    name=_ROLE,
    description=(
        "Complete one independently adjudicated finding in place at "
        "findings/FIND-NNN.md: evidence-backed impact, CVSS 3.1 via cvss_score, "
        "reproduction, limits and remediation. Never create a second finding copy."
    ),
    factory=create_finding_reporter,
    parent_agents=("decepticon",),
    bundle="standard",
)


if is_bundle_enabled("standard"):
    graph = create_finding_reporter()
