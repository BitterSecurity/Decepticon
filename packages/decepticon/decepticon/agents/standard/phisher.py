"""Phisher Agent - initial-access via phishing / social engineering.

Specialist for the ``INITIAL_ACCESS`` kill-chain phase via T1566.*
(phishing) techniques. Real-world Red Teams reach initial access via
phishing ~74% of the time (Verizon DBIR), making this agent the
defining gap-closer for engagement realism.

Tool surface (all delegated to bash inside the sandbox; no fancy SDK
required to start):

  - GoPhish campaign builder (campaigns, groups, landing pages, templates).
  - evilginx2 phishlet + lure manager.
  - O365 / Workspace OAuth device-code helper.
  - Lookalike-domain registration / Punycode generator.
  - SES / Mailgun campaign send orchestration.
  - Lure-deconfliction handshake (out-of-band ping the blue-team contact
    in plan/roe.json BEFORE the campaign sends).

Skills tree (under packages/decepticon/decepticon/skills/standard/phisher/):

  - agents/prompts/workflows/phisher.md  loop + scope + handoff format
    (inlined into the system prompt at factory time; not a skill to load)
  - pretext-engineering/     pretext design (LinkedIn / Hunter.io chains)
  - gophish-campaign/        gophish API, template authoring
  - evilginx2-proxy/         evilginx2 phishlets, lure links
  - o365-credential-harvest/ OAuth device-code attack
  - lookalike-domain/        Punycode + DNS / TLS provisioning
  - lure-deconfliction/      mandatory pre-send handshake with blue team
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
from decepticon.tools.references.tools import (
    killchain_lookup,
    methodology_lookup,
    payload_search,
)
from decepticon.tools.web.search import web_fetch, web_search
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

# KG tools were removed pending the Neo4j middleware redesign (see
# docs/design/neo4j-research-notes.md). KG surface is currently limited
# to the analyst agent.
_STANDARD_TOOLS: dict[str, Any] = {
    t.name: t
    for t in [
        payload_search,
        methodology_lookup,
        killchain_lookup,
        # Open-web acquisition (ADR-0010): target/pretext recon for phishing
        web_search,
        web_fetch,
        *BASH_TOOLS,
    ]
}


_ROLE = "phisher"
_RECURSION_LIMIT = 250
_SKILL_SOURCES: list[str] = ["/skills/standard/phisher/", "/skills/shared/"]


def create_phisher_agent(
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
    """Build the Phisher agent.

    Specialist factory for initial-access via phishing. Same factory
    shape as the other ``agents/standard/*.py`` specialists. See
    ``packages/decepticon/decepticon/agents/standard/ad_operator.py`` for
    the canonical pattern.
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
        create_phisher_agent()
    )  # lgtm[py/unused-global-variable]  # consumed by langgraph at runtime


SUBAGENT_SPEC = SubAgentSpec(
    name="phisher",
    description=(
        "Initial-access specialist via phishing / social engineering "
        "(MITRE T1566.*). Use when the engagement's INITIAL_ACCESS "
        "objective requires user interaction - email phishing, "
        "evilginx2 token capture, M365 OAuth device-code, lookalike "
        "domain registration. Always coordinates the lure-deconfliction "
        "handshake with the blue team via plan/roe.json before sending."
    ),
    factory=create_phisher_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=15,
)
