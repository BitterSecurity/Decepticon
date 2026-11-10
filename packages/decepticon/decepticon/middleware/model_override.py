"""Runtime model override — power for the CLI ``/model`` command.

Reads ``model_override`` from runtime context or the declared graph state
channel (set by CLI run input or resume updates) and rebinds the LLM
for the wrapped invocation. The override is per-call, not per-process,
so flipping the model in the REPL takes effect on the next ``submit()``
without restarting the agent or rebuilding the langgraph stack.

Resolution order, in priority:

  1. ``request.runtime.context.model_override`` (set by Runtime context)
  2. ``request.state["model_override"]`` (set by input state field)

When neither is set, the wrapped handler is called with the original
LLM untouched (the ``factory.get_model("decepticon")`` baked in at
agent construction).

The override value can be:
  - A LiteLLM model id like ``anthropic/claude-opus-4-7`` or
    ``groq/llama-3.3-70b-versatile`` — gets routed through the same
    LiteLLM proxy the rest of the stack uses.
  - An empty string or None — equivalent to no override.

Failure modes:
  - Unknown / malformed model id surfaces at the next provider call
    as a 404 wrapped through ``_reraise_with_actionable_message``;
    middleware does not pre-validate (kept dumb so the operator can
    experiment with brand-new model ids without a release).
"""

from __future__ import annotations

from typing import Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from typing_extensions import Annotated, NotRequired, override

from decepticon.llm.factory import LLMFactory
from decepticon.middleware.run_controls import (
    ainvoke_with_run_fallbacks,
    invoke_with_run_fallbacks,
    merge_run_controls,
    prepare_runtime_request,
)
from decepticon.middleware.state_reducers import reduce_converging_value
from decepticon_core.contracts.host_integration import get_host_integration
from decepticon_core.types.llm import LLMModelMapping
from decepticon_core.utils.logging import get_logger

log = get_logger("middleware.model_override")


def _read_override(request: Any, role: str | None = None) -> str:
    """Pull the override id out of runtime context or input state.

    Two shapes are honoured, in priority order:

      1. ``model_overrides`` — a ``{role: model_id}`` MAP. When ``role`` is
         present in the map, that per-agent model wins. This is the
         PER-AGENT policy surface — a multi-tenant host (e.g. the SaaS)
         threads one map per run so ``soundwave`` and ``exploit`` can run
         a different model than the rest WITHOUT an OSS release. Set once
         on the run (Runtime context or the ``model_overrides`` state
         channel, which propagates to sub-agents like ``proxy_api_key``).
      2. ``model_override`` — a single model id applied to EVERY agent
         (the CLI ``/model`` command). Fallback when the map has no entry
         for this role.

    Returns the empty string when nothing is set so the caller can
    short-circuit with a single truthiness check.
    """

    def _pick(container: Any) -> str:
        if not isinstance(container, dict):
            return ""
        if role:
            overrides = container.get("model_overrides")
            if isinstance(overrides, dict):
                value = overrides.get(role, "")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        value = container.get("model_override", "")
        return value.strip() if isinstance(value, str) and value.strip() else ""

    runtime = getattr(request, "runtime", None)
    if runtime is not None:
        ctx = getattr(runtime, "context", None) or {}
        picked = _pick(ctx if isinstance(ctx, dict) else {})
        if picked:
            return picked
    state = getattr(request, "state", None) or {}
    if hasattr(state, "get"):
        picked = _pick(
            {
                "model_overrides": state.get("model_overrides"),
                "model_override": state.get("model_override", ""),
            }
        )
        if picked:
            return picked
    return ""


def _build_proxied_llm(model_id: str, original: BaseChatModel) -> BaseChatModel:
    """Use the factory's protocol and streaming settings for runtime overrides."""
    factory = LLMFactory(mapping=LLMModelMapping())
    return factory._create_chat_model(model_id, getattr(original, "temperature", None))


class ModelOverridesState(AgentState):
    """Declares global and per-role model channels on the orchestrator graph.

    Mirrors ``ProxyKeyState`` (see ``proxy_key_override``). The Platform
    DROPS an undeclared key from run ``input``, so without this channel a
    SaaS caller threading a per-role model map via run ``input`` could
    never reach ``request.state`` (the middleware's reachable read path;
    ``runtime.context`` needs a top-level ``context`` field the Platform
    forbids alongside ``config.configurable``). Set once on the kickoff
    run, persisted to the checkpoint, read on every model call.

    Reducer — ``reduce_converging_value`` (NOT the default LastValue):
    the orchestrator dispatches sub-agents in the SAME super-step and
    deepagents copies parent state (incl. ``model_overrides``) into each,
    so every sub-agent writes the map back to the parent in one tick. A
    LastValue channel rejects >1 write per step; a converging reducer
    keeps the (identical) non-None write. Same treatment ``proxy_api_key``
    and the launcher-set context channels already use.
    """

    model_overrides: NotRequired[Annotated[dict, reduce_converging_value]]
    model_override: NotRequired[Annotated[str, reduce_converging_value]]
    run_controls: NotRequired[Annotated[dict, merge_run_controls]]


class ModelOverrideMiddleware(AgentMiddleware):
    """Per-invocation model swap driven by Runtime context / input state.

    Wired into every agent's middleware stack ahead of
    ``ModelFallbackMiddleware`` so the override picks the new primary
    and the existing fallback chain still applies on its failure.

    ``role`` is the agent this instance is attached to (the slot factory
    passes it). It lets a per-role ``model_overrides`` map target this
    agent specifically; without it only the global ``model_override``
    (CLI ``/model``) applies.
    """

    state_schema = ModelOverridesState

    def __init__(self, role: str | None = None) -> None:
        super().__init__()
        self._role = role
        self._host = get_host_integration()
        self._host_policy = self._host.read_policy()

    @override
    def wrap_model_call(self, request, handler):
        policy = self._host_policy
        if policy is not None:
            self._host.require_current(policy)
            selection = self._host.model_selection(policy, self._role)
            model = _build_proxied_llm(selection.model, request.model)
            settings = dict(request.model_settings)
            if selection.effort is not None:
                settings["reasoning_effort"] = selection.effort
            self._host.require_current(policy)
            result = handler(
                prepare_runtime_request(
                    request.override(model=model, model_settings=settings),
                    self._role,
                    host_policy=True,
                )
            )
            self._host.require_current(policy)
            self._host.observe_dispatch(policy, self._role, request)
            return result
        override_id = _read_override(request, self._role)
        if not override_id:
            return invoke_with_run_fallbacks(
                prepare_runtime_request(request, self._role), handler, role=self._role
            )
        try:
            new_llm = _build_proxied_llm(override_id, request.model)
        except Exception as exc:
            log.warning("model_override %s failed to bind: %s", override_id, exc)
            return invoke_with_run_fallbacks(
                prepare_runtime_request(request, self._role), handler, role=self._role
            )
        log.info("model_override active: %s", override_id)
        return invoke_with_run_fallbacks(
            prepare_runtime_request(request.override(model=new_llm), self._role),
            handler,
            role=self._role,
        )

    @override
    async def awrap_model_call(self, request, handler):
        policy = self._host_policy
        if policy is not None:
            self._host.require_current(policy)
            selection = self._host.model_selection(policy, self._role)
            model = _build_proxied_llm(selection.model, request.model)
            settings = dict(request.model_settings)
            if selection.effort is not None:
                settings["reasoning_effort"] = selection.effort
            self._host.require_current(policy)
            result = await handler(
                prepare_runtime_request(
                    request.override(model=model, model_settings=settings),
                    self._role,
                    host_policy=True,
                )
            )
            self._host.require_current(policy)
            self._host.observe_dispatch(policy, self._role, request)
            return result
        override_id = _read_override(request, self._role)
        if not override_id:
            return await ainvoke_with_run_fallbacks(
                prepare_runtime_request(request, self._role), handler, role=self._role
            )
        try:
            new_llm = _build_proxied_llm(override_id, request.model)
        except Exception as exc:
            log.warning("model_override %s failed to bind: %s", override_id, exc)
            return await ainvoke_with_run_fallbacks(
                prepare_runtime_request(request, self._role), handler, role=self._role
            )
        log.info("model_override active: %s", override_id)
        return await ainvoke_with_run_fallbacks(
            prepare_runtime_request(request.override(model=new_llm), self._role),
            handler,
            role=self._role,
        )


__all__ = ["ModelOverrideMiddleware"]
