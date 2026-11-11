"""Host integration seam — a host-neutral provider the engine consults.

A trusted library *host* (a desktop app, a multi-tenant control plane, …) may
provision out-of-band authority — which model each role runs, which external
tools are approved, dispatch observation — into the running engine. The engine
core must not carry any one host's schema or naming; it consults a provider
through this seam instead.

The default is :class:`NullHostIntegration`: ``read_policy()`` returns ``None``
and every consumer falls through to ordinary OSS behavior. A host ships its
implementation in a plugin package and registers it under the
``decepticon.host_integration`` entry-point group; the single registered
provider (allowlist-gated like plugin bundles) replaces the null default.

This mirrors the trusted-library-provider precedent documented in the extension
contract (e.g. ``planning_document_source=``): absence preserves the existing
client contract. Consumers always guard on ``read_policy() is not None`` before
calling the policy-taking methods, so the null provider never reaches them.

Core treats the policy as an **opaque handle**: ``read_policy()`` returns an
object the provider itself interprets. :class:`HostModelSelection` is the only
structured value that crosses the seam back into core.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from importlib.metadata import entry_points
from threading import RLock
from typing import Any, Protocol, runtime_checkable

from decepticon_core.plugin_loader import is_bundle_enabled

HOST_INTEGRATION_GROUP = "decepticon.host_integration"


class HostPolicyError(RuntimeError):
    """A host-provisioned authority was violated or is no longer current.

    Carries a short machine ``code``. The engine propagates this rather than
    swallowing it (see ``run_controls._must_propagate``); a host implementation
    raises it (or a subclass) from its provider methods.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HostModelSelection:
    """The model a host selected for one role: a model id and optional effort."""

    model: str
    effort: str | None = None


@runtime_checkable
class HostIntegration(Protocol):
    """What the engine consults when a host provisions authority.

    Every ``policy`` argument is the opaque object returned by ``read_policy()``.
    langchain types (tools, model requests) are typed ``Any`` to keep
    ``decepticon-core`` free of a langchain dependency.
    """

    def read_policy(self) -> Any | None:
        """Return the current host policy handle, or ``None`` when no host
        has provisioned one (pure OSS / red-only / CLI)."""
        ...

    def require_current(self, policy: Any) -> None:
        """Raise :class:`HostPolicyError` if ``policy`` is no longer the
        current provisioned authority (host atomic replacement revokes it)."""
        ...

    def model_selection(self, policy: Any, role: str | None) -> HostModelSelection:
        """The model/effort the host selected for ``role``."""
        ...

    def observe_dispatch(self, policy: Any, role: str | None, request: Any) -> None:
        """Record the tools offered on a dispatch for host-side observation."""
        ...

    def policy_tools(self, role: str) -> list[Any]:
        """Host-approved external tools to add to ``role``'s toolset."""
        ...

    def mcp_list_connections(self, policy: Any) -> str:
        """JSON string: the host-approved MCP-style connections."""
        ...

    def mcp_search(self, policy: Any, connection: str, query: str, limit: int) -> str:
        """JSON string: host-approved tools matching the query."""
        ...

    def mcp_tool_schema(self, policy: Any, connection: str, tool_name: str) -> str:
        """JSON string: schema/description for one host-approved tool."""
        ...

    def mcp_call(self, policy: Any, connection: str, tool_name: str, arguments_json: str) -> str:
        """Dispatch one host-approved tool and return its JSON result."""
        ...

    def server_routes(self) -> list[Any]:
        """Server routers the host contributes (e.g. a settings endpoint)."""
        ...


class NullHostIntegration:
    """Default provider — no host present. Reproduces today's ``None`` path."""

    def read_policy(self) -> Any | None:
        return None

    def require_current(self, policy: Any) -> None:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def model_selection(
        self, policy: Any, role: str | None
    ) -> HostModelSelection:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def observe_dispatch(
        self, policy: Any, role: str | None, request: Any
    ) -> None:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def policy_tools(self, role: str) -> list[Any]:
        return []

    def mcp_list_connections(self, policy: Any) -> str:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def mcp_search(
        self, policy: Any, connection: str, query: str, limit: int
    ) -> str:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def mcp_tool_schema(
        self, policy: Any, connection: str, tool_name: str
    ) -> str:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def mcp_call(
        self, policy: Any, connection: str, tool_name: str, arguments_json: str
    ) -> str:  # pragma: no cover - unreachable
        raise HostPolicyError("no_host_integration")

    def server_routes(self) -> list[Any]:
        return []


_NULL: HostIntegration = NullHostIntegration()
_resolved: HostIntegration | None = None
_override: HostIntegration | None = None
_registry_lock = RLock()


def _discover_provider() -> HostIntegration:
    """No host is optional; broken or ambiguous installed authority is not.

    A deployment may require an entry-point name with
    ``DECEPTICON_HOST_INTEGRATION``. Missing/disabled required providers and
    load/discovery failures refuse rather than silently selecting no policy.
    """
    required = os.environ.get("DECEPTICON_HOST_INTEGRATION", "").strip()
    if required and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", required) is None:
        raise HostPolicyError("invalid_host_integration_selection")
    chosen: HostIntegration | None = None
    try:
        eps = list(entry_points(group=HOST_INTEGRATION_GROUP))
    except Exception:
        raise HostPolicyError("host_integration_discovery_failed") from None
    if required:
        eps = [ep for ep in eps if ep.name == required]
        if len(eps) != 1:
            raise HostPolicyError("required_host_integration_unavailable")
    for ep in eps:
        try:
            obj = ep.load()
            # A class or a zero-arg factory is instantiated; a ready provider
            # instance (it has ``read_policy``) is used as-is.
            if isinstance(obj, type):
                provider = obj()
            elif callable(obj) and not hasattr(obj, "read_policy"):
                provider = obj()
            else:
                provider = obj
            if not is_bundle_enabled(getattr(provider, "bundle", None)):
                if required:
                    raise HostPolicyError("required_host_integration_disabled")
                continue
            if not isinstance(provider, HostIntegration):
                raise HostPolicyError("invalid_host_integration_provider")
        except HostPolicyError:
            raise
        except Exception:
            # Plugin failures can contain credential material. Propagate only
            # the stable refusal code, without logging arbitrary exceptions.
            raise HostPolicyError("host_integration_load_failed") from None
        if chosen is not None:
            raise HostPolicyError("ambiguous_host_integration")
        chosen = provider
    return chosen if chosen is not None else _NULL


def get_host_integration() -> HostIntegration:
    """The active provider: an explicit override, else the resolved plugin
    provider (cached), else the null default."""
    global _resolved
    with _registry_lock:
        if _override is not None:
            return _override
        if _resolved is None:
            _resolved = _discover_provider()
        return _resolved


def set_host_integration(provider: HostIntegration | None) -> None:
    """Explicitly set (or clear) the provider, bypassing discovery. For hosts
    that wire the provider directly, and for tests."""
    global _override
    with _registry_lock:
        if provider is not None and not isinstance(provider, HostIntegration):
            raise HostPolicyError("invalid_host_integration_provider")
        _override = provider


def reset_host_integration_cache() -> None:
    """Drop the cached discovery result (tests / re-resolution after install)."""
    global _resolved
    with _registry_lock:
        _resolved = None
