"""Decepticon — AI-powered autonomous red team testing framework."""

import os as _os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    # pyproject.toml carries a "0.0.0" sentinel; release.yml stamps the
    # real tag into the package metadata at Docker build time, and
    # importlib.metadata reads it back here. Local checkouts read 0.0.0.
    __version__ = _version("decepticon")
except PackageNotFoundError:
    __version__ = "0.0.0"

__package_name__ = "decepticon"

# Legacy ``decepticon.sandbox_*`` imports resolve to ``decepticon_sandbox``.
from decepticon import _sandbox_aliases as _sandbox_aliases  # noqa: E402

_sandbox_aliases.install()

# Phase 2 framework boot — register OSS roles with RoleRegistry,
# materialize the PluginRegistry singleton, and surface the legacy
# import paths via the compat shim. All idempotent / opt-out via env.
#
# The sandbox runtime lives in the separate ``decepticon_sandbox`` package
# and never imports this one. DECEPTICON_SKIP_BOOT=1 remains an explicit
# opt-out for embedders that need the package without the registries.
if not _os.environ.get("DECEPTICON_SKIP_BOOT"):
    from decepticon import _boot, compat

    _boot.run()
    compat.register_legacy_imports()
