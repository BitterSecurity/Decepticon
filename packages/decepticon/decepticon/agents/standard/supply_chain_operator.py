"""SupplyChainOperator Agent - software supply-chain attack lane.

Supply-chain skills already exist in the repo at
``packages/decepticon/decepticon/skills/standard/supply-chain/`` but no
agent consumed them. This file adds the missing dispatch surface so
OPPLAN objectives tagged with T1195.* / T1199 route to a dedicated
specialist.

Note: the role name uses underscores (``supply_chain_operator``) while
the skill directory uses a hyphen (``supply-chain``), so this agent
sets ``_SKILL_SOURCES`` explicitly rather than relying on the
``/skills/standard/{role}/`` default.

Tool surface (all via bash for the OSS bootstrap):

  - npm / pip / cargo registry probes for dependency-confusion +
    typosquat discovery.
  - syft / grype for SBOM generation + diffing.
  - git + CI config parsers for poisoned-pipeline-execution review.
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
from decepticon.tools.research.tools import (
    cve_lookup,
    kg_add_edge,
    kg_add_node,
    kg_neighbors,
    kg_query,
    kg_stats,
)
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        kg_add_node,
        kg_add_edge,
        kg_query,
        kg_neighbors,
        kg_stats,
        cve_lookup,
        payload_search,
        methodology_lookup,
        *BASH_TOOLS,
    ]
}


_ROLE = "supply_chain_operator"
_RECURSION_LIMIT = 250
_SKILL_SOURCES: list[str] = ["/skills/standard/supply-chain/", "/skills/shared/"]


def create_supply_chain_operator_agent(
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
    """Build the SupplyChainOperator agent."""
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
        create_supply_chain_operator_agent()
    )  # lgtm[py/unused-global-variable]  # consumed by langgraph at runtime


SUBAGENT_SPEC = SubAgentSpec(
    name="supply_chain_operator",
    description=(
        "Software supply-chain attack specialist. Use for dependency "
        "confusion, typosquatting, malicious packages (npm / PyPI / "
        "crates), CI/CD pipeline and build-step compromise, and SBOM / "
        "registry abuse (MITRE T1195.*, T1199). Existing skill tree at "
        "skills/standard/supply-chain/."
    ),
    factory=create_supply_chain_operator_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=25,
)
