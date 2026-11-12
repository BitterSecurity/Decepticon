"""Deprecated import aliases for the relocated sandbox runtime.

The sandbox runtime moved from ``decepticon.sandbox_{kernel,server,web,browser}``
to the top-level ``decepticon_sandbox.{kernel,server,web,browser}`` package.
Importing an old path returns the canonical module object itself (registered
under both names), so classes keep a single identity across old and new paths.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
import warnings
from types import ModuleType

_PREFIXES: dict[str, str] = {
    "decepticon.sandbox_kernel": "decepticon_sandbox.kernel",
    "decepticon.sandbox_server": "decepticon_sandbox.server",
    "decepticon.sandbox_web": "decepticon_sandbox.web",
    "decepticon.sandbox_browser": "decepticon_sandbox.browser",
}
_warned: set[str] = set()


def _canonical(fullname: str) -> str | None:
    for legacy, canonical in _PREFIXES.items():
        if fullname == legacy or fullname.startswith(legacy + "."):
            return canonical + fullname[len(legacy) :]
    return None


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, canonical: str) -> None:
        self._canonical = canonical

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType:
        root = spec.name.split(".")[:2]
        legacy_root = ".".join(root)
        if legacy_root not in _warned:
            _warned.add(legacy_root)
            warnings.warn(
                f"{legacy_root} moved to {_canonical(legacy_root)}; update the import",
                DeprecationWarning,
                stacklevel=3,
            )
        return importlib.import_module(self._canonical)

    def exec_module(self, module: ModuleType) -> None:
        # The canonical module is already executed; the import system keeps
        # its __name__/__spec__ and registers it under the legacy name too.
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        canonical = _canonical(fullname)
        if canonical is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(canonical))


def install() -> None:
    if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
