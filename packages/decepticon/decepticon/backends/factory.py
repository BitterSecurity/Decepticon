"""Backend factory — HTTP-transport sandbox builder.

The agent code shouldn't know how the sandbox is deployed; it just asks
for a sandbox object. ``build_sandbox_backend()`` returns an
``HTTPSandbox`` that talks to a sandbox daemon over HTTP, which works in
every deployment target Decepticon supports today:

  - Dev / local-docker: sandbox container exposes the FastAPI daemon
    on ``http://sandbox:9999`` over the shared ``sandbox-net`` network.
  - Per-VM silo plane: sandbox sibling container on the VM, daemon
    reachable on loopback.
  - Cloud Run pool plane: sandbox runs as a sidecar in the same
    Cloud Run revision, reachable on ``localhost:9999`` via the shared
    network namespace.

There is no longer a docker-exec transport: the previous DockerSandbox
path required mounting ``/var/run/docker.sock`` into the langgraph
container, which is a host-escape vector for any prompt-injection-driven
RCE inside the agent process. HTTP-only consolidates on a single tested
code path and keeps the sandbox blast radius bounded by the container
boundary + the ``sandbox-net`` network.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

from decepticon.backends.http_sandbox import HTTPSandbox

_DEFAULT_SANDBOX_URL = "http://localhost:9999"
_openshell_resolver: Callable[[Any], HTTPSandbox] | None = None


class _UnboundOpenShellSandbox(HTTPSandbox):
    """A graph-construction placeholder; it can never perform an operation."""

    egress_authority = "openshell"

    def __init__(self) -> None:
        super().__init__("http://127.0.0.1:9999")

    @property
    def id(self) -> str:
        return "openshell:unbound"

    def _http(self):
        raise RuntimeError("OpenShell run binding is required; direct HTTP is disabled")

    def _request(self, method: str, path: str, **kwargs: Any):
        raise RuntimeError("OpenShell run binding is required before execution")


def configure_openshell_resolver(resolver: Callable[[Any], HTTPSandbox] | None) -> None:
    """Install a TRUSTED host resolver, never one supplied by agent config.

    It must authenticate owner/run/custody, resolve the opaque binding reference
    against host state, and cache one admitted backend per binding. A plain
    sandbox ID or a model-generated admission receipt is not authorization.
    """
    global _openshell_resolver
    _openshell_resolver = resolver


def _run_config(config: Any = None) -> dict:
    if config is not None:
        return config if isinstance(config, dict) else {}
    try:
        from langgraph.config import get_config

        return dict(get_config() or {})
    except RuntimeError:
        return {}


# Sized for the multi-tenant case: a single SHARED langgraph process can serve
# many concurrent engagements, each routed (via the bash tool's per-run
# ``configurable.sandbox_url`` — see ``tools/bash/bash.py:_sandbox_from_config``)
# to its OWN per-engagement sandbox. Each must keep its own client so the
# ``SandboxNotificationMiddleware._jobs`` view stays consistent within a run;
# under-sizing would evict a live engagement's client mid-flight. 128 covers
# realistic per-process concurrency with headroom.
@functools.lru_cache(maxsize=128)
def _shared_sandbox(base_url: str, token: str | None) -> HTTPSandbox:
    return HTTPSandbox(base_url=base_url, token=token)


def _endpoint_from_configurable(configurable: Any) -> tuple[str | None, str | None]:
    """Extract ``(sandbox_url, sandbox_token)`` from a ``configurable`` mapping."""
    if not isinstance(configurable, dict):
        return None, None
    raw_url = configurable.get("sandbox_url")
    raw_token = configurable.get("sandbox_token")
    url = raw_url if isinstance(raw_url, str) and raw_url else None
    token = raw_token if isinstance(raw_token, str) and raw_token else None
    return url, token


def _resolve_endpoint(config: Any = None) -> tuple[str, str | None]:
    """Resolve the sandbox ``(base_url, token)``, preferring per-run config.

    A shared langgraph process serving many engagements cannot reach a
    per-engagement sandbox through one process-wide env var. Resolution order:

    1. An **explicitly-passed** run ``config`` (``config.configurable.sandbox_url``
       / ``sandbox_token``). This is the reliable source inside a SUB-AGENT: the
       tool/middleware holds the run's ``runtime.config`` and passes it here.
       The ambient ``get_config()`` contextvar (step 2) is NOT seeded in a
       sub-agent's tool-execution context, so relying on it alone routed
       sub-agent filesystem ops to the env sidecar instead of the run's own
       per-engagement sandbox (bash, which reads its injected ``config``, did
       reach the right sandbox — the two diverged).
    2. The ambient ``get_config()`` contextvar (top-level orchestrator path).
    3. ``SANDBOX_URL`` / ``SANDBOX_TOKEN`` env (single-tenant / sidecar /
       local-docker / import-time construction with no active run).
    """
    url: str | None = None
    token: str | None = None

    # 1) Explicit run config passed by the caller (sub-agent-safe).
    if config is not None:
        url, token = _endpoint_from_configurable((config or {}).get("configurable"))

    # 2) Ambient contextvar (raises outside a runnable context → fall through).
    if url is None:
        try:
            from langgraph.config import get_config

            cv_url, cv_token = _endpoint_from_configurable((get_config() or {}).get("configurable"))
            url = cv_url
            if token is None:
                token = cv_token
        except Exception:
            pass

    # 3) Env fallback.
    if url is None:
        url = os.environ.get("SANDBOX_URL", _DEFAULT_SANDBOX_URL)
    if token is None:
        token = os.environ.get("SANDBOX_TOKEN") or None
    return url, token


def build_sandbox_backend(config: Any = None) -> HTTPSandbox:
    """Build the HTTP-transport sandbox backend.

    ``config`` — an optional run ``RunnableConfig``. When provided its
    ``configurable.sandbox_url`` / ``sandbox_token`` take precedence over the
    ambient ``get_config()`` contextvar (see ``_resolve_endpoint``). Callers that
    hold the run's config explicitly (the filesystem middleware, which has
    ``runtime.config``) MUST pass it so filesystem ops reach the run's own
    per-engagement sandbox even inside a sub-agent, where the contextvar is not
    seeded.

    Returns the same ``HTTPSandbox`` instance for every call with the
    same ``(base_url, token)``. langgraph dev server invokes one factory
    per registered graph at startup; without a shared client each
    factory builds its own client + its own ``BackgroundJobTracker``,
    and the ``SandboxNotificationMiddleware`` instance held by each
    graph sees a different ``_jobs`` view than the bash tool actually
    registers against — completion notifications never reach the agent.
    Keying by ``(base_url, token)`` keeps tests that monkeypatch the env
    isolated and supports multi-tenant deployments where a shared process routes
    each run to a distinct per-engagement daemon.

    Endpoint resolution (see ``_resolve_endpoint``): the current run's
    LangGraph ``configurable.sandbox_url`` / ``sandbox_token`` win when
    present; otherwise ``SANDBOX_URL`` / ``SANDBOX_TOKEN`` apply.

    Returns:
        An ``HTTPSandbox`` instance pointed at the daemon URL.

    Env:
        SANDBOX_URL
            Base URL of the sandbox daemon. Default
            ``http://localhost:9999`` (sibling-container / sidecar
            loopback). Compose sets this to ``http://sandbox:9999``.
        SANDBOX_TOKEN
            Optional bearer token for daemon auth — recommended even on
            loopback as defence-in-depth.
    """
    run_config = _run_config(config)
    configurable = run_config.get("configurable", {})
    host_provider = os.environ.get("DECEPTICON_SANDBOX_PROVIDER", "").strip()
    if host_provider not in ("", "http", "openshell"):
        raise RuntimeError("Unsupported host sandbox provider")
    requested = configurable.get("sandbox_provider") if isinstance(configurable, dict) else None
    if host_provider == "openshell" and requested not in (None, "openshell"):
        raise RuntimeError("Host requires OpenShell; provider downgrade is disabled")
    if host_provider == "openshell" or requested == "openshell":
        if _openshell_resolver is None:
            raise RuntimeError("OpenShell provider requires a trusted host admission resolver")
        if not run_config:
            return _UnboundOpenShellSandbox()
        from decepticon.backends.openshell_sandbox import OpenShellSandbox

        backend = _openshell_resolver(run_config)
        if not isinstance(backend, OpenShellSandbox):
            raise RuntimeError("OpenShell resolver did not return an admitted OpenShell backend")
        backend.check_admission()
        return backend
    base_url, token = _resolve_endpoint(config)
    return _shared_sandbox(base_url, token)
