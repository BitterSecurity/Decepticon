"""Compose the same capability layers through LangChain's direct factory."""

from typing import Any

from deepagents.graph import DeepAgentState
from deepagents.middleware._parent_notifier import _ParentNotifierMiddleware
from deepagents.middleware._prompt_caching import append_prompt_caching_middleware
from deepagents.middleware._state import private_state_field_names
from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.memory import MemoryMiddleware
from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware
from deepagents.middleware.subagents import SubAgentMiddleware
from deepagents.middleware.unsupported_content import UnsupportedContentMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, HumanInTheLoopMiddleware


def compose(**kwargs: Any) -> Any:
    model, backend = kwargs["model"], kwargs["backend"]
    middleware: list[AgentMiddleware[Any, Any, Any]] = [FilesystemMiddleware(backend=backend)]
    if kwargs.get("subagents"):
        middleware.append(SubAgentMiddleware(backend=backend, subagents=kwargs["subagents"]))
    middleware.extend(
        [
            _ParentNotifierMiddleware(),
            PatchToolCallsMiddleware(),
        ]
    )
    middleware.extend(kwargs.get("middleware", []))
    append_prompt_caching_middleware(middleware)
    if kwargs.get("memory"):
        middleware.append(
            MemoryMiddleware(backend=backend, sources=kwargs["memory"], add_cache_control=True)
        )
    if kwargs.get("interrupt_on"):
        middleware.append(HumanInTheLoopMiddleware(interrupt_on=kwargs["interrupt_on"]))
    middleware.extend(
        [
            UnsupportedContentMiddleware(),
            _ToolExclusionMiddleware(
                excluded=frozenset({"ls", "edit_file", "delete", "glob", "grep"})
            ),
        ]
    )
    keys = private_state_field_names(
        DeepAgentState, *(m.state_schema for m in middleware if getattr(m, "state_schema", None))
    )
    for layer in middleware:
        if isinstance(layer, SubAgentMiddleware):
            layer.private_state_keys |= keys
    return create_agent(
        model=model,
        system_prompt=kwargs["system_prompt"],
        middleware=middleware,
        state_schema=DeepAgentState,
        checkpointer=kwargs.get("checkpointer"),
        name=kwargs["name"],
    )
