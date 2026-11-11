"""Host integration surface — the stable API for applications that run the framework.

A *host* runs Decepticon agents inside its own process: the OSS clients, a
desktop host, or a downstream product that uses the framework as a library.
Plugin authors extend the framework through ``decepticon_sdk``'s contracts;
hosts additionally need to route sandboxes per run, compose agents, and carry
per-run state. This module is the single import point for that, re-exporting
framework implementations without moving them.

Install with ``pip install 'decepticon-sdk[host]'`` (pulls in ``decepticon``).

Names exported here are covered by the SDK stability manifest: removing or
renaming one is a breaking change under RELEASE.md. Internal module paths
behind them may change freely; import from here, not from those paths.

Groups:

* Per-run configuration — :class:`HostRunConfigurable` declares the
  ``config["configurable"]`` keys the framework reads.
* Sandbox runtime — build the per-run sandbox backend, install the trusted
  OpenShell admission resolver, and tell OpenShell-transport backends apart.
* Per-run rebinding — re-resolve a composed backend against the current run.
* Agent assembly — compose middleware/tools/models with plugin overrides
  applied, and build the planning agent.
* State channels — agent-state schemas and reducers a host writes through run
  input (per-role model overrides, LLM proxy key, sub-agent transcripts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from decepticon.agents.build import (
        SafetyOverrideViolation,
        build_middleware,
        build_tools,
        iter_override_bundles,
        resolve_prompt_overrides,
        resolve_role_model,
    )
    from decepticon.backends import (
        SKILLS_LOCAL_PATH,
        HTTPTransportSandbox,
        build_sandbox_backend,
        configure_openshell_resolver,
        is_openshell_sandbox,
        make_agent_backend,
    )
    from decepticon.backends.openshell_sandbox import OpenShellSandbox
    from decepticon.llm import LLMFactory
    from decepticon.middleware.filesystem import (
        EngagementFilesystemBackend,
        rebind_sandbox_per_run,
        workspace_from_runtime,
    )
    from decepticon.middleware.model_override import ModelOverridesState
    from decepticon.middleware.proxy_key_override import ProxyKeyState
    from decepticon.middleware.state_reducers import reduce_converging_value
    from decepticon.middleware.subagent_transcript_state import SubagentTranscriptState
    from decepticon.tools.bash.bash import get_sandbox, set_sandbox
except ModuleNotFoundError as exc:  # pragma: no cover - exercised without the framework
    if exc.name is not None and exc.name.split(".")[0] == "decepticon":
        raise ImportError(
            "decepticon_sdk.host requires the Decepticon framework; install it with "
            "`pip install 'decepticon-sdk[host]'`"
        ) from exc
    raise

from decepticon_core.types.host import HOST_RUN_CONFIGURABLE_KEYS, HostRunConfigurable

if TYPE_CHECKING:
    from decepticon.agents.standard.soundwave import create_soundwave_agent

# Importing the Soundwave module builds its default graph (and LLM clients) at
# import time, so it is resolved only when a host actually asks for it.
_LAZY: dict[str, tuple[str, str]] = {
    "create_soundwave_agent": ("decepticon.agents.standard.soundwave", "create_soundwave_agent"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


__all__ = [
    # Per-run configuration
    "HOST_RUN_CONFIGURABLE_KEYS",
    "HostRunConfigurable",
    # Sandbox runtime
    "SKILLS_LOCAL_PATH",
    "HTTPTransportSandbox",
    "OpenShellSandbox",
    "build_sandbox_backend",
    "configure_openshell_resolver",
    "get_sandbox",
    "is_openshell_sandbox",
    "make_agent_backend",
    "set_sandbox",
    # Per-run rebinding
    "EngagementFilesystemBackend",
    "rebind_sandbox_per_run",
    "workspace_from_runtime",
    # Agent assembly
    "LLMFactory",
    "SafetyOverrideViolation",
    "build_middleware",
    "build_tools",
    "create_soundwave_agent",
    "iter_override_bundles",
    "resolve_prompt_overrides",
    "resolve_role_model",
    # State channels
    "ModelOverridesState",
    "ProxyKeyState",
    "SubagentTranscriptState",
    "reduce_converging_value",
]
