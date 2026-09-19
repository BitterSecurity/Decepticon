"""Autohunt Agent — autonomous single-target engagement bootstrap.

Autohunt is an additive planning lane.  Soundwave remains the default
interview-first planner and keeps its existing graph identifier and behavior.
"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent

from decepticon.agents.build import build_middleware, build_tools
from decepticon.agents.prompts import load_prompt
from decepticon.agents.standard.soundwave import _STANDARD_TOOLS
from decepticon.backends import build_sandbox_backend, make_agent_backend
from decepticon.llm import LLMFactory
from decepticon_core.plugin_loader import is_bundle_enabled, load_plugin_callbacks

_ROLE = "soundwave"
_GRAPH_NAME = "autohunt"
_RECURSION_LIMIT = 200


def create_autohunt_agent(
    *,
    backend: Any = None,
    llm: Any = None,
    fallback_models: list | None = None,
    tools: list[Any] | None = None,
    middleware: list[Any] | None = None,
    system_prompt: str | None = None,
    recursion_limit: int | None = None,
):
    """Build the additive Autohunt bootstrap planner.

    The lane deliberately reuses Soundwave's document-only tool and middleware
    contract, while its separate prompt consumes trusted launcher target context
    instead of starting the standard interview.
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
        system_prompt = load_prompt(_GRAPH_NAME)

    return create_agent(
        llm,
        system_prompt=system_prompt,
        tools=tools,
        middleware=middleware,
        name=_GRAPH_NAME,
    ).with_config(
        {
            "recursion_limit": recursion_limit or _RECURSION_LIMIT,
            "callbacks": load_plugin_callbacks(role=_ROLE, backend=backend),
        }
    )


if is_bundle_enabled("standard"):
    graph = create_autohunt_agent()
