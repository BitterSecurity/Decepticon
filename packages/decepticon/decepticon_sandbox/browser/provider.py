"""Browser provider interface and registry (sandbox side).

A provider owns one browser per engagement workspace inside the sandbox and
translates :mod:`decepticon_sandbox.browser.contract` actions into its native
interface. Network scope is NOT a provider responsibility: the sandbox's
OpenShell network policy (compiled from ``plan/roe.json``) enforces every
connection the browser makes, including redirects and subresources. Providers
must therefore run their browser inside this sandbox and must not route
traffic to a browser hosted elsewhere.

Built-in providers are listed in ``_BUILTIN``. Additional providers register
under the ``decepticon.browser_providers`` entry-point group with a factory
``(workspace: Path) -> BrowserProvider``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from decepticon_sandbox.browser.contract import BrowserRequest, BrowserResult

DEFAULT_PROVIDER = "obscura"
ENTRY_POINT_GROUP = "decepticon.browser_providers"

_BUILTIN: dict[str, str] = {
    "obscura": "decepticon_sandbox.browser.providers.obscura:ObscuraProvider",
}


@runtime_checkable
class BrowserProvider(Protocol):
    name: str

    def capabilities(self) -> frozenset[str]:
        """Contract actions this provider implements."""
        ...

    def invoke(self, request: BrowserRequest) -> BrowserResult:
        """Run one validated action against the workspace's browser."""
        ...

    def shutdown(self) -> None:
        """Stop the workspace's browser and release its resources."""
        ...


class UnknownProviderError(LookupError):
    pass


def _load(target: str) -> Callable[[Path], BrowserProvider]:
    module_name, _, attr = target.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def available_providers() -> list[str]:
    names = set(_BUILTIN)
    names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


def load_provider(name: str, workspace: Path) -> BrowserProvider:
    if name in _BUILTIN:
        factory = _load(_BUILTIN[name])
    else:
        matches = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name == name]
        if not matches:
            raise UnknownProviderError(
                f"unknown browser provider {name!r}; available: {', '.join(available_providers())}"
            )
        factory = matches[0].load()
    provider = factory(workspace)
    if not isinstance(provider, BrowserProvider):
        raise TypeError(f"browser provider {name!r} does not implement BrowserProvider")
    return provider
