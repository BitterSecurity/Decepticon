from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon.tools.defense.blue_sensor import (
    blue_sensor_body,
    blue_sensor_events,
    blue_sensor_scan,
)
from decepticon_core.plugin_loader import SubAgentSpec, is_bundle_enabled, load_plugin_callbacks

_STANDARD_TOOLS: dict[str, Any] = {
    "blue_sensor_scan": blue_sensor_scan,
    "blue_sensor_events": blue_sensor_events,
    "blue_sensor_body": blue_sensor_body,
}


_ROLE = "blue_cell"
_RECURSION_LIMIT = 120


def create_blue_cell_agent(
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
    """Build a sensor-grounded defensive agent with read-only tools."""
    if llm is None or fallback_models is None:
        factory = LLMFactory()
        if llm is None:
            llm = factory.get_model(_ROLE)
        if fallback_models is None:
            fallback_models = factory.get_fallback_models(_ROLE)

    # No set_sandbox() here — Blue Cell intentionally has no bash tool.
    sandbox = build_sandbox_backend()

    if backend is None:
        backend = make_agent_backend(sandbox)

    if tools is None:
        tools = build_tools(role=_ROLE, standard_tools=_STANDARD_TOOLS)
    if middleware is None:
        middleware = build_middleware(
            role=_ROLE,
            backend=backend,
            llm=llm,
            fallback_models=fallback_models,
            sandbox=None,  # no SandboxNotification for the read-only Blue Cell
        )
    if system_prompt is None:
        system_prompt = load_prompt(_ROLE, shared=[])

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
    graph = create_blue_cell_agent()


SUBAGENT_SPEC = SubAgentSpec(
    name="blue_cell",
    description=(
        "Blue Cell — independent read-only defender. Investigates live local "
        "target telemetry and the always-on monitor's incidents, then reports "
        "evidence and uncertainty. No bash or containment tools."
    ),
    factory=create_blue_cell_agent,
    parent_agents=("decepticon",),
    bundle="standard",
    priority=90,
)
