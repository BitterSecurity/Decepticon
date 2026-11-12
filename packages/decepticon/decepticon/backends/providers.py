"""Sandbox provider selection — the single source of truth.

A sandbox provider is the runtime behind the agent's sandbox backend. It is
chosen by the host with ``DECEPTICON_SANDBOX_PROVIDER``:

* ``openshell`` (default) — OpenShell sandbox born confined by the policy
  compiled from ``plan/roe.json``; the only provider with a kernel network
  boundary.
* ``docker`` — the plain Kali sandbox container reached through its daemon
  (compose ``sandbox`` service or a per-run ``sandbox_url``). No network
  boundary; for tests, benchmarks and pre-scope work. ``http`` is a
  deprecated alias.
* any name registered under the ``decepticon.sandbox_providers`` entry-point
  group with a factory ``(run_config: dict) -> sandbox backend`` (for
  example a hosted runtime such as E2B).

A run may request ``openshell`` on a non-OpenShell host through
``configurable.sandbox_provider``; it can never downgrade an OpenShell host.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable
from importlib.metadata import entry_points
from typing import Any

PROVIDER_ENV = "DECEPTICON_SANDBOX_PROVIDER"
DEFAULT_PROVIDER = "openshell"
BUILTIN_PROVIDERS: frozenset[str] = frozenset({"openshell", "docker"})
ENTRY_POINT_GROUP = "decepticon.sandbox_providers"
_DEPRECATED_ALIASES: dict[str, str] = {"http": "docker"}


class UnknownSandboxProviderError(RuntimeError):
    pass


def normalize_provider(name: str | None) -> str | None:
    """Lower-case, strip and resolve deprecated aliases; ``None`` when empty."""
    if not isinstance(name, str):
        return None
    value = name.strip().lower()
    if not value:
        return None
    if value in _DEPRECATED_ALIASES:
        replacement = _DEPRECATED_ALIASES[value]
        warnings.warn(
            f"sandbox provider {value!r} is deprecated; use {replacement!r}",
            DeprecationWarning,
            stacklevel=3,
        )
        return replacement
    return value


def _plugin_names() -> set[str]:
    return {ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)}


def available_providers() -> list[str]:
    return sorted(BUILTIN_PROVIDERS | _plugin_names())


def host_provider() -> str:
    """The host's provider from ``DECEPTICON_SANDBOX_PROVIDER`` (default openshell)."""
    name = normalize_provider(os.environ.get(PROVIDER_ENV)) or DEFAULT_PROVIDER
    if name not in BUILTIN_PROVIDERS and name not in _plugin_names():
        raise UnknownSandboxProviderError(
            f"unknown sandbox provider {name!r}; available: {', '.join(available_providers())}"
        )
    return name


def requested_provider(configurable: Any) -> str | None:
    """The provider a run requests via ``configurable.sandbox_provider``."""
    if not isinstance(configurable, dict):
        return None
    return normalize_provider(configurable.get("sandbox_provider"))


def uses_openshell(configurable: Any) -> bool:
    """True when this run's sandbox must come from the OpenShell resolver."""
    return host_provider() == "openshell" or requested_provider(configurable) == "openshell"


def plugin_factory(name: str) -> Callable[[dict], Any]:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name == name:
            return ep.load()
    raise UnknownSandboxProviderError(f"sandbox provider {name!r} is not registered")
