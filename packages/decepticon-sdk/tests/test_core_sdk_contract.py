"""Contract: every SDK re-export from core is the identical object (`is`).

Plugin authors import from ``decepticon_sdk`` expecting the same class /
function objects that live in ``decepticon_core``. A silent wrapper or
copy would break ``isinstance`` checks and identity-based registries.

The name list is driven from ``decepticon_sdk.__all__`` so a new
re-export automatically enters this contract; the origin map below must
be updated in the same commit (KeyError = missing origin). Symbols that
are deliberately SDK-owned (not re-exports) live in
``_SDK_OWNED_ALLOWLIST`` with a documented reason.

Does not touch the public-API count lock in
``test_sdk_api_stability.py`` (``expected = 30``) — this file adds no
new public names.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

import decepticon_sdk

# Names in ``decepticon_sdk.__all__`` that are NOT re-exports of a core
# object. Each entry must carry a one-line reason.
_SDK_OWNED_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Computed via importlib.metadata for the decepticon-sdk
        # distribution; mirrors core's own ``__version__`` pattern but
        # is a distinct string attribute, not a re-export.
        "__version__",
    }
)

# Canonical core module for every re-exported name. Mirrors the import
# block in ``decepticon_sdk/__init__.py``; keep them in lockstep.
_CORE_ORIGIN: dict[str, str] = {
    # Protocols (decepticon_core.protocols)
    "AgentProtocol": "decepticon_core.protocols",
    "BackendProtocol": "decepticon_core.protocols",
    "CallbackProtocol": "decepticon_core.protocols",
    "LLMProtocol": "decepticon_core.protocols",
    "MiddlewareProtocol": "decepticon_core.protocols",
    "SandboxProtocol": "decepticon_core.protocols",
    "ToolProtocol": "decepticon_core.protocols",
    # Slot enum + constants (decepticon_core.contracts.slots)
    "MiddlewareSlot": "decepticon_core.contracts.slots",
    "SAFETY_CRITICAL_SLOTS": "decepticon_core.contracts.slots",
    "SLOTS_PER_ROLE": "decepticon_core.contracts.slots",
    # Plugin contributions (decepticon_core.contracts.contributions)
    "MiddlewareContribution": "decepticon_core.contracts.contributions",
    "PromptContribution": "decepticon_core.contracts.contributions",
    "SafetyDeclaration": "decepticon_core.contracts.contributions",
    "SubAgentContribution": "decepticon_core.contracts.contributions",
    "ToolContribution": "decepticon_core.contracts.contributions",
    # Plugin loader (decepticon_core.plugin_loader)
    "PluginBundle": "decepticon_core.plugin_loader",
    "SubAgentSpec": "decepticon_core.plugin_loader",
    "is_bundle_enabled": "decepticon_core.plugin_loader",
    # Registries (decepticon_core.registry)
    "PluginConflictWarning": "decepticon_core.registry",
    "PluginInfo": "decepticon_core.registry",
    "PluginRegistry": "decepticon_core.registry",
    "RoleRegistry": "decepticon_core.registry",
    "RoleResolution": "decepticon_core.registry",
    "RoleSpec": "decepticon_core.registry",
    "SafetyRegistry": "decepticon_core.registry",
    "SkillSourceRegistry": "decepticon_core.registry",
}


def _reexport_names() -> list[str]:
    """Public SDK names that must be identical to a core object."""
    return sorted(name for name in decepticon_sdk.__all__ if name not in _SDK_OWNED_ALLOWLIST)


def test_allowlist_subset_of_all() -> None:
    """Allowlist entries must actually appear in ``__all__`` (no drift)."""
    unknown = _SDK_OWNED_ALLOWLIST - set(decepticon_sdk.__all__)
    assert unknown == set(), (
        f"_SDK_OWNED_ALLOWLIST has names not in decepticon_sdk.__all__: {sorted(unknown)}"
    )


def test_origin_map_covers_every_reexport() -> None:
    """Every non-allowlisted ``__all__`` name has a core origin entry."""
    missing = set(_reexport_names()) - set(_CORE_ORIGIN)
    extra = set(_CORE_ORIGIN) - set(_reexport_names())
    assert missing == set(), (
        f"_CORE_ORIGIN missing entries for new SDK re-exports: {sorted(missing)}. "
        f"Add the canonical decepticon_core module path (see decepticon_sdk/__init__.py)."
    )
    assert extra == set(), (
        f"_CORE_ORIGIN has stale entries not in decepticon_sdk.__all__: {sorted(extra)}"
    )


@pytest.mark.parametrize("name", _reexport_names())
def test_sdk_reexport_is_core_identical(name: str) -> None:
    """``decepticon_sdk.<name> is decepticon_core.<module>.<name>``."""
    origin = _CORE_ORIGIN[name]
    core_mod = importlib.import_module(origin)
    sdk_obj: Any = getattr(decepticon_sdk, name)
    core_obj: Any = getattr(core_mod, name)
    assert sdk_obj is core_obj, (
        f"decepticon_sdk.{name} is not the identical object as {origin}.{name}. "
        f"SDK must re-export core symbols by reference; if this name is a "
        f"deliberate wrapper, add it to _SDK_OWNED_ALLOWLIST with a reason."
    )
