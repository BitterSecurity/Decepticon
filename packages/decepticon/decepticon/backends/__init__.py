import importlib.resources
from collections.abc import Mapping
from typing import Any

from deepagents.backends import CompositeBackend, FilesystemBackend

from .factory import build_sandbox_backend, configure_openshell_resolver, is_openshell_sandbox
from .http_transport import (
    HTTPSandbox,  # back-compat alias re-export
    HTTPTransportSandbox,
)

# Skills ship as package data under ``decepticon/skills/`` and are read
# in-process by a local ``FilesystemBackend`` (not the sandbox container).
# Resolving via ``importlib.resources`` yields the correct on-disk location
# for every install shape — wheel (site-packages), editable (repo checkout),
# and the langgraph Docker image (``/app/decepticon/skills``) — so no
# container-specific path is hardcoded.
SKILLS_LOCAL_PATH = str(importlib.resources.files("decepticon") / "skills")


# Caller-supplied ``extra_routes`` keys are rejected if they match any
# of these — preserving the OSS skill tree and preventing root-prefix
# shadowing that would route every request through the caller's backend.
# Spec §16.4 #5: longest-prefix-wins gives the intended override semantics
# only when callers mount under a SUB-prefix of OSS defaults, never at or
# above them. Downstream overlays use ``/skills/tenant/<id>/`` etc.
_RESERVED_PREFIXES: frozenset[str] = frozenset({"/skills/", "/", ""})


def _validate_extra_route_key(prefix: str) -> None:
    """Reject keys that would shadow OSS defaults or traverse paths.

    Raises ``ValueError`` on:
      * empty string or bare ``/`` — would route ALL paths through the
        caller's backend, bypassing the sandbox transport.
      * ``/skills/`` exactly — would replace the OSS skill tree wholesale
        (substituting attacker-controlled context into every model turn).
      * keys missing the leading or trailing slash — ambiguous prefix
        semantics; longest-prefix-wins assumes ``/foo/`` form.
      * keys containing ``..`` — path traversal attempt.
    """
    if not isinstance(prefix, str):
        raise ValueError(f"extra_routes keys must be str, got {type(prefix).__name__}")
    if prefix in _RESERVED_PREFIXES:
        raise ValueError(
            f"extra_routes key {prefix!r} is reserved; "
            f"mount tenant/plugin overlays under a sub-prefix such as "
            f"'/skills/tenant/<id>/' or '/skills/plugins/<name>/'"
        )
    if not prefix.startswith("/") or not prefix.endswith("/"):
        raise ValueError(
            f"extra_routes key {prefix!r} must be an absolute prefix in "
            f"'/.../' form (leading + trailing slash required)"
        )
    if ".." in prefix:
        raise ValueError(
            f"extra_routes key {prefix!r} contains '..' — path traversal patterns are not allowed"
        )


def make_agent_backend(
    sandbox: Any,
    *,
    extra_routes: Mapping[str, Any] | None = None,
) -> CompositeBackend:
    """Compose the runtime backend for a Decepticon agent.

    Routes ``/skills/`` to a local ``FilesystemBackend`` reading the
    package's ``decepticon/skills`` tree in-process, and routes everything
    else (notably ``/workspace/``) through the sandbox transport
    (``HTTPTransportSandbox``). Returning a ``CompositeBackend`` lets
    ``SkillsMiddleware`` and ``FilesystemMiddleware`` share the same
    backend object while reading from different physical storage:

      /skills/...   ->  decepticon/skills/... read in-process (~5ms)
      /workspace/.. ->  sandbox container via HTTP (isolated, persistent)

    Args:
        sandbox: the default transport (``HTTPTransportSandbox`` in OSS). All
            paths that don't match a more specific route fall through
            here.
        extra_routes: optional caller-supplied prefix -> backend mapping
            merged on top of the OSS defaults. Closes gap §8 #1 from
            the downstream consumption audit: downstream overlays mount their
            own asset trees (``/skills/plugins/apt-emulation/``, etc.)
            without forking ``make_agent_backend``. Per spec §16.4 #5,
            routes are sorted by descending prefix length so the longest
            match wins deterministically — a tenant-specific
            ``/skills/tenant/<id>/`` route overrides the default
            ``/skills/`` prefix.

            Reserved prefixes ``""`` / ``"/"`` / ``"/skills/"`` and any
            key not in ``"/.../"`` form (or containing ``..``) are
            rejected with ``ValueError`` — preventing root shadowing and
            wholesale OSS skill-tree substitution.

    Raises:
        ValueError: when an ``extra_routes`` key violates the prefix
            rules above.
    """
    routes = dict(extra_routes or {})
    for prefix in routes:
        _validate_extra_route_key(prefix)

    base: dict[str, Any] = {
        "/skills/": FilesystemBackend(
            root_dir=SKILLS_LOCAL_PATH,
            virtual_mode=True,
        ),
    }
    merged: dict[str, Any] = {**base, **routes}
    # Longest-prefix-wins: sort by len(prefix) descending so a tenant
    # path like ``/skills/tenant/<id>/`` always matches before the
    # generic ``/skills/`` default.
    sorted_routes = dict(sorted(merged.items(), key=lambda kv: len(kv[0]), reverse=True))
    return CompositeBackend(default=sandbox, routes=sorted_routes)


__all__ = [
    "HTTPTransportSandbox",
    "HTTPSandbox",
    "SKILLS_LOCAL_PATH",
    "build_sandbox_backend",
    "configure_openshell_resolver",
    "is_openshell_sandbox",
    "make_agent_backend",
]
