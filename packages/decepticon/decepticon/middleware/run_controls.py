"""Validated, checkpoint-scoped runtime controls and observable model routing."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Annotated, Any, Literal

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger(__name__)
ModelID = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,255}$")]


class RunControls(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    fallback_models: Annotated[tuple[ModelID, ...], Field(max_length=8)] | None = None
    effort: Literal["auto", "low", "medium", "high", "max"] = "auto"
    autocompact: Literal["auto"] | Annotated[int, Field(strict=True, ge=1024, le=10_000_000)] = (
        "auto"
    )

    @field_validator("fallback_models")
    @classmethod
    def unique_models(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("fallback model IDs must be unique")
        return value


def merge_run_controls(current: dict | None, update: dict | None) -> dict:
    """Keep omitted settings on resume; parallel children carry convergent values."""
    if (current is not None and not isinstance(current, dict)) or (
        update is not None and not isinstance(update, dict)
    ):
        raise ValueError("run_controls must be an object")
    merged = {**(current or {}), **(update or {})}
    return RunControls.model_validate(merged).model_dump(mode="json", exclude_unset=True)


def read_run_controls(request: Any) -> RunControls:
    state = getattr(request, "state", None) or {}
    value = state.get("run_controls", {}) if hasattr(state, "get") else {}
    context = getattr(getattr(request, "runtime", None), "context", None)
    if isinstance(context, dict) and "run_controls" in context:
        value = merge_run_controls(value, context["run_controls"])
    return RunControls.model_validate({} if value is None else value)


def emit_custom_event(event_type: str, payload: dict[str, Any]) -> None:
    try:
        from langgraph.config import get_stream_writer

        writer = get_stream_writer()
        if writer is not None:
            writer({"type": event_type, **payload})
    except Exception:
        log.debug("runtime visibility event unavailable", exc_info=True)


def emit_runtime_event(request: Any, event_type: str, payload: dict[str, Any]) -> None:
    emit_custom_event(event_type, payload)
    state = getattr(request, "state", None) or {}
    if not hasattr(state, "get"):
        return
    workspace, engagement = state.get("workspace_path"), state.get("engagement_name")
    if (
        not isinstance(workspace, str)
        or not workspace
        or not isinstance(engagement, str)
        or not engagement
    ):
        return
    try:
        from decepticon.runtime.event_log import EventLog

        EventLog.for_workspace(workspace, engagement).append(event_type, payload)
    except Exception:
        log.debug("runtime event persistence unavailable", exc_info=True)


def model_name(model: Any) -> str:
    value = getattr(model, "model_name", None) or getattr(model, "model", None)
    return value[:256] if isinstance(value, str) else type(model).__name__


def _context_window(model: str | None) -> int | None:
    """Max input tokens for the model, from LiteLLM's cost map. None if unknown."""
    if not model:
        return None
    try:
        from litellm import model_cost  # noqa: PLC0415

        entry = model_cost.get(model) or model_cost.get(model.split("/", 1)[-1])
        window = (entry or {}).get("max_input_tokens")
        return int(window) if window else None
    except Exception:
        return None


def prepare_runtime_request(request: Any, role: str | None, *, host_policy: bool = False) -> Any:
    controls = read_run_controls(request)
    settings = dict(getattr(request, "model_settings", None) or {})
    if not host_policy and controls.effort != "auto":
        settings["reasoning_effort"] = controls.effort
        request = request.override(model_settings=settings)
    try:
        context_tokens = int(count_tokens_approximately(getattr(request, "messages", []) or []))
    except Exception:
        context_tokens = None
    emit_runtime_event(
        request,
        "runtime_status",
        {
            "agent": role or "",
            "model": model_name(getattr(request, "model", None)),
            "effort": settings.get("reasoning_effort") or "provider default",
            "autocompact": controls.autocompact,
            "context_tokens": context_tokens,
            "context_window": _context_window(model_name(getattr(request, "model", None))),
        },
    )
    return request


def _must_propagate(error: Exception) -> bool:
    from langgraph.errors import GraphBubbleUp

    from decepticon.middleware.budget import (
        BudgetCredentialCheckUnavailable,
        BudgetExceeded,
        BudgetSpendUntracked,
    )
    from decepticon_core.contracts.host_integration import HostPolicyError

    return isinstance(
        error,
        (
            GraphBubbleUp,
            HostPolicyError,
            BudgetExceeded,
            BudgetSpendUntracked,
            BudgetCredentialCheckUnavailable,
        ),
    )


def invoke_with_run_fallbacks(request: Any, handler: Callable, *, role: str | None = None) -> Any:
    controls = read_run_controls(request)
    if controls.fallback_models is None:
        return handler(request)
    try:
        return handler(request)
    except Exception as error:
        if _must_propagate(error):
            raise
        last_error = error
    from decepticon.middleware.model_override import _build_proxied_llm

    for attempt, name in enumerate(controls.fallback_models, start=1):
        try:
            model = _build_proxied_llm(name, request.model)
            emit_runtime_event(
                request,
                "model_fallback_engaged",
                {"agent": role or "", "model": name, "attempt": attempt},
            )
            return handler(request.override(model=model))
        except Exception as error:
            if _must_propagate(error):
                raise
            last_error = error
    raise last_error


async def ainvoke_with_run_fallbacks(
    request: Any, handler: Callable, *, role: str | None = None
) -> Any:
    controls = read_run_controls(request)
    if controls.fallback_models is None:
        return await handler(request)
    try:
        return await handler(request)
    except Exception as error:
        if _must_propagate(error):
            raise
        last_error = error
    from decepticon.middleware.model_override import _build_proxied_llm

    for attempt, name in enumerate(controls.fallback_models, start=1):
        try:
            model = _build_proxied_llm(name, request.model)
            emit_runtime_event(
                request,
                "model_fallback_engaged",
                {"agent": role or "", "model": name, "attempt": attempt},
            )
            return await handler(request.override(model=model))
        except Exception as error:
            if _must_propagate(error):
                raise
            last_error = error
    raise last_error


class VisibleModelFallbackMiddleware(ModelFallbackMiddleware):
    """Observe configured fallbacks; explicit session chains are owned by the outer override."""

    def wrap_model_call(self, request, handler):
        if read_run_controls(request).fallback_models is not None:
            return handler(request)
        attempt = 0

        def observed(candidate):
            nonlocal attempt
            if candidate.model is not request.model:
                attempt += 1
                emit_runtime_event(
                    candidate,
                    "model_fallback_engaged",
                    {"model": model_name(candidate.model), "attempt": attempt},
                )
            return handler(candidate)

        return super().wrap_model_call(request, observed)

    async def awrap_model_call(self, request, handler):
        if read_run_controls(request).fallback_models is not None:
            return await handler(request)
        attempt = 0

        async def observed(candidate):
            nonlocal attempt
            if candidate.model is not request.model:
                attempt += 1
                emit_runtime_event(
                    candidate,
                    "model_fallback_engaged",
                    {"model": model_name(candidate.model), "attempt": attempt},
                )
            return await handler(candidate)

        return await super().awrap_model_call(request, observed)
