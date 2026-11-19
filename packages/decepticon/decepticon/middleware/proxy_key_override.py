"""Per-run LiteLLM virtual-key override — multi-tenant cost attribution.

In a SHARED langgraph serving many orgs, the baked-in model authenticates to
the LiteLLM proxy with the env master key (``DECEPTICON_LLM__PROXY_API_KEY``),
so ALL spend lands on the master key and can't be split per customer. The SaaS
launch flow mints a virtual key per engagement (the key's ``team_id`` is the
org) and threads it in as ``config.configurable.proxy_api_key``. This middleware
reads that key and rebinds the model for the wrapped call to authenticate with
it, so LiteLLM attributes the spend to that key's team (= the org) — the basis
for per-customer billing + budget enforcement (``/team/info`` spend per org).

Slot placement: ``PROXY_KEY_OVERRIDE`` sits AFTER ``MODEL_FALLBACK`` in the
canonical slot order, i.e. inner-most relative to model selection. So it
re-keys whatever model is actually about to be called — including a fallback
model the fallback middleware swapped in, and any model the model-override
middleware selected. Keep that ordering or fallbacks mis-attribute.

Resolution order (mirrors ``model_override``):

  1. ``request.runtime.context.proxy_api_key`` (Runtime context)
  2. ``request.state["proxy_api_key"]`` (input state)

When neither is set the wrapped handler runs with the original env-keyed model
untouched — so single-tenant / OSS deployments (no per-run key) are unaffected.

The key value is never logged here, and ``event_logging`` already redacts
``api_key`` fields, so the virtual key does not leak into event streams.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from typing_extensions import NotRequired, override

from decepticon.llm.factory import LLMFactory, _model_drops_temperature
from decepticon.middleware.state_reducers import reduce_converging_value
from decepticon_core.utils.logging import get_logger

log = get_logger("middleware.proxy_key_override")


class ProxyKeyState(AgentState):
    """State surface for the per-run virtual key.

    Declared so the orchestrator graph has a ``proxy_api_key`` channel — the
    Platform DROPS an undeclared key from run input, so without this channel a
    SaaS caller threading the key via run ``input`` could never reach
    ``request.state`` (the middleware's reachable read path; ``runtime.context``
    needs a top-level ``context`` field, which the Platform forbids alongside
    ``config.configurable`` that OSS already uses for tenant context). Set once
    on the kickoff run, persisted to the checkpoint, and read on every model
    call (incl. resume runs that send no input). Extends ``AgentState`` so the
    agent's own channels are preserved.

    Reducer — ``reduce_converging_value`` (NOT the default LastValue): the
    orchestrator can dispatch two sub-agents in the SAME super-step (the model
    emits multiple ``task()`` tool calls in one turn). deepagents copies parent
    state into each sub-agent and does NOT exclude ``proxy_api_key``, so each
    sub-agent's task() Command writes the key back to the parent in that single
    tick. A LastValue channel rejects >1 write per step with
    ``InvalidUpdateError`` ("Can receive only one value per step. Use an
    Annotated key…"), which crashed the whole run and left ``thread.values``
    without ``objectives`` / ``subagent_transcripts``. Every writer carries the
    SAME per-run key, so a converging reducer (keep the non-None write) collapses
    the concurrent writes to one value — the same treatment the launcher-set
    context channels (``target_url``, ``language`` …) already use.

    The value is sensitive (a LiteLLM virtual key). It is never logged here;
    ``event_logging`` redacts ``api_key`` fields; the SaaS stream + state
    routes redact it from client-facing surfaces. It does persist into the
    checkpoint + internal LangSmith trace state — an accepted internal-only
    exposure (NOT run metadata, so it does not regress the metadata-leak fix).
    """

    proxy_api_key: NotRequired[Annotated[str, reduce_converging_value]]


def _read_proxy_key(request: Any) -> str:
    """Pull the per-run virtual key out of runtime context or input state.

    Returns the empty string when nothing is set so the caller can
    short-circuit with a single truthiness check.
    """
    runtime = getattr(request, "runtime", None)
    if runtime is not None:
        ctx = getattr(runtime, "context", None) or {}
        if isinstance(ctx, dict):
            value = ctx.get("proxy_api_key", "")
            if isinstance(value, str) and value.strip():
                return value.strip()
    state = getattr(request, "state", None) or {}
    get = state.get if hasattr(state, "get") else (lambda _k, _d=None: None)
    value = get("proxy_api_key", "") or ""
    return value.strip() if isinstance(value, str) else ""


def _rekey_model(original: BaseChatModel, api_key: str) -> BaseChatModel:
    """Bind fresh proxy clients while retaining the selected model's protocol.

    The OpenAI clients capture credentials at construction, so a shallow model
    copy would retain the previous key. Revalidate the original concrete class
    with all its configuration, including fields excluded from serialization
    (custom HTTP transports, callbacks and streaming timeouts). Only the bound
    OpenAI clients must be recreated. Never send a virtual key to an arbitrary
    model URL: the destination remains the configured proxy.
    """
    if not isinstance(original, ChatOpenAI):
        raise TypeError("per-run proxy credentials require an OpenAI-compatible proxy model")
    proxy = LLMFactory._resolve_proxy_config()
    bound_clients = {"client", "async_client", "root_client", "root_async_client"}
    kwargs = {
        name: getattr(original, name)
        for name in type(original).model_fields
        if name not in bound_clients
    }
    kwargs["openai_api_base"] = proxy.url
    kwargs["openai_api_key"] = SecretStr(api_key)
    # Default headers can override the client's new Authorization header. Keep
    # provider BYOK and tracing headers, but replace proxy authentication only.
    if original.default_headers is not None:
        kwargs["default_headers"] = {
            name: api_key if name.lower() == "x-litellm-api-key" else value
            for name, value in original.default_headers.items()
            if name.lower() != "authorization"
        }
    if _model_drops_temperature(original.model_name):
        kwargs["temperature"] = None
    return type(original).model_validate(kwargs)


class ProxyKeyOverrideMiddleware(AgentMiddleware):
    """Per-invocation LiteLLM key swap driven by Runtime context / input state.

    No-op when no per-run key is present, so OSS / single-tenant deployments
    behave exactly as before.
    """

    # Register the ``proxy_api_key`` channel on every graph that mounts this
    # slot, so a SaaS caller can deliver the key via run ``input`` (the channel
    # the Platform actually persists into ``request.state``). create_agent
    # merges this into the compiled graph state.
    state_schema = ProxyKeyState

    @override
    def wrap_model_call(self, request, handler):
        key = _read_proxy_key(request)
        if not key:
            return handler(request)
        try:
            new_llm = _rekey_model(request.model, key)
        except Exception:
            log.warning(
                "proxy_key_override refused dispatch: per-run proxy credential binding failed"
            )
            raise RuntimeError(
                "Failed to bind per-run proxy credential; model dispatch refused"
            ) from None
        log.info("proxy_key_override active (per-run virtual key)")
        return handler(request.override(model=new_llm))

    @override
    async def awrap_model_call(self, request, handler):
        key = _read_proxy_key(request)
        if not key:
            return await handler(request)
        try:
            new_llm = _rekey_model(request.model, key)
        except Exception:
            log.warning(
                "proxy_key_override refused dispatch: per-run proxy credential binding failed"
            )
            raise RuntimeError(
                "Failed to bind per-run proxy credential; model dispatch refused"
            ) from None
        log.info("proxy_key_override active (per-run virtual key)")
        return await handler(request.override(model=new_llm))


__all__ = ["ProxyKeyOverrideMiddleware"]
