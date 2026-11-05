"""IotOperator Agent - IoT / embedded-device attack lane.

IoT skills already exist in the repo at
``packages/decepticon/decepticon/skills/standard/iot/`` (firmware,
BLE, Zigbee, Z-Wave, sub-GHz, LoRaWAN, U-Boot, /dev/mem) but no agent
consumed them. This file adds the missing dispatch surface so OPPLAN
objectives tagged with embedded / IoT TTPs route to a real specialist.

Tool surface (all via bash for the OSS bootstrap):

  - binwalk / firmware-mod-kit for firmware extraction.
  - bettercap / gatttool / bleah for BLE GATT.
  - killerbee / zbstumbler for Zigbee.
  - rtl_433 / hackrf / gqrx for sub-GHz + radio capture.
  - chirpstack helpers for LoRaWAN OTAA / ABP.

Hardware: BLE / Zigbee / sub-GHz work needs an SDR or radio dongle
passed through to the sandbox; the default OSS sandbox does not ship
radio hardware. Firmware static analysis runs without hardware.
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


_ROLE = "iot_operator"
_RECURSION_LIMIT = 250
_SKILL_SOURCES: list[str] = ["/skills/standard/iot/", "/skills/shared/"]


def create_iot_operator_agent(
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
    """Build the IotOperator agent."""
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
    graph = create_iot_operator_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="iot_operator",
    description=(
        "IoT / embedded-device attack specialist. Use when scope "
        "includes IoT or embedded hardware: firmware acquisition + "
        "binwalk extraction, hardcoded-credential discovery, bootloader "
        "(U-Boot) and /dev/mem attacks, and wireless radios (BLE GATT, "
        "Zigbee Touchlink, Z-Wave, sub-GHz, LoRaWAN). Existing skill "
        "tree at skills/standard/iot/."
    ),
    factory=create_iot_operator_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=58,
)
