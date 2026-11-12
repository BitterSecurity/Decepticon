"""Backend factory — builds the agent sandbox for the selected provider.

The provider is chosen once per host in :mod:`decepticon.backends.providers`
(``DECEPTICON_SANDBOX_PROVIDER``):

  - ``openshell`` (default): the trusted admission resolver returns an
    ``OpenShellSandbox`` born confined by the policy compiled from the RoE.
  - ``docker`` (``http`` is a deprecated alias): an ``HTTPTransportSandbox``
    talking to the plain sandbox daemon — compose ``http://sandbox:9999``, a
    sidecar on loopback, or a run's ``configurable.sandbox_url``. No network
    boundary.
  - plugins registered under ``decepticon.sandbox_providers``: the entry
    point's factory receives the run config.

There is no docker-exec transport: mounting ``/var/run/docker.sock`` into the
langgraph container would be a host-escape vector.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

from deepagents.backends.protocol import (
    FileDownloadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)

from decepticon.backends.http_transport import HTTPTransportSandbox

_DEFAULT_SANDBOX_URL = "http://localhost:9999"
_openshell_resolver: Callable[[Any], HTTPTransportSandbox] | None = None


class _UnboundOpenShellSandbox(HTTPTransportSandbox):
    """A graph-construction placeholder; it can never perform an operation.

    Read-class operations are the one carve-out: before a scope is imported
    the workspace is simply empty, so reads report not-found / empty results
    instead of raising — graph-construction-time hooks (OPPLAN load,
    engagement markers) then see an empty workspace and the operator journey
    reaches ``/scope import``. Note ``HTTPTransportSandbox.read`` is execute-based
    (the sandbox daemon reads files with a script), so these overrides are
    implemented directly and never touch the transport. Every other
    operation stays loud: without an admitted binding there is no kernel
    boundary to execute anything behind.
    """

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

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        # Pre-admission the workspace is empty: every requested path reports
        # the daemon's own not-found convention instead of raising.
        return [FileDownloadResponse(path=p, content=None, error="file_not_found") for p in paths]

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return ReadResult(error=f"File '{file_path}': file_not_found")

    def ls(self, path: str) -> LsResult:
        return LsResult(error=None, entries=[])

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(error=None, matches=[], truncated=False, truncation_reason=None)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        return GrepResult(error=None, matches=[], truncated=False)


def configure_openshell_resolver(resolver: Callable[[Any], HTTPTransportSandbox] | None) -> None:
    """Install a TRUSTED host resolver, never one supplied by agent config.

    It must authenticate owner/run/custody, resolve the opaque binding reference
    against host state, and cache one admitted backend per binding. A plain
    sandbox ID or a model-generated admission receipt is not authorization.
    """
    global _openshell_resolver
    _openshell_resolver = resolver


def is_openshell_sandbox(backend: object) -> bool:
    """Return whether ``backend`` is an OpenShell-transport sandbox.

    True for an admitted :class:`~decepticon.backends.openshell_sandbox.OpenShellSandbox`
    and for the pre-admission placeholder this factory returns before a scope is
    imported. The check is by type, never by an attribute a caller could set, so
    a host can use it to decide which backends sit behind the kernel boundary.
    """
    from decepticon.backends.openshell_sandbox import OpenShellSandbox

    return isinstance(backend, (OpenShellSandbox, _UnboundOpenShellSandbox))


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
def _shared_sandbox(base_url: str, token: str | None) -> HTTPTransportSandbox:
    return HTTPTransportSandbox(base_url=base_url, token=token)


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


def build_sandbox_backend(config: Any = None) -> HTTPTransportSandbox:
    """Build the HTTP-transport sandbox backend.

    ``config`` — an optional run ``RunnableConfig``. When provided its
    ``configurable.sandbox_url`` / ``sandbox_token`` take precedence over the
    ambient ``get_config()`` contextvar (see ``_resolve_endpoint``). Callers that
    hold the run's config explicitly (the filesystem middleware, which has
    ``runtime.config``) MUST pass it so filesystem ops reach the run's own
    per-engagement sandbox even inside a sub-agent, where the contextvar is not
    seeded.

    Returns the same ``HTTPTransportSandbox`` instance for every call with the
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
        An ``HTTPTransportSandbox`` instance pointed at the daemon URL.

    Env:
        SANDBOX_URL
            Base URL of the sandbox daemon. Default
            ``http://localhost:9999`` (sibling-container / sidecar
            loopback). Compose sets this to ``http://sandbox:9999``.
        SANDBOX_TOKEN
            Optional bearer token for daemon auth — recommended even on
            loopback as defence-in-depth.
    """
    from decepticon.backends.providers import (
        BUILTIN_PROVIDERS,
        host_provider,
        plugin_factory,
        requested_provider,
    )

    run_config = _run_config(config)
    configurable = run_config.get("configurable", {})
    # OpenShell is the only network-boundary authority since the nftables
    # retirement (#1144) and the default provider. ``docker`` (formerly
    # ``http``) and plugin providers serve non-OpenShell runtimes.
    host = host_provider()
    requested = requested_provider(configurable)
    if host == "openshell" and requested not in (None, "openshell"):
        raise RuntimeError("Host requires OpenShell; provider downgrade is disabled")
    if host == "openshell" or requested == "openshell":
        if _openshell_resolver is None:
            # The operator's explicit env opt-in is what makes the local
            # resolver trusted — agent config can never install one.
            from decepticon.backends.openshell_local import maybe_install_local_resolver

            maybe_install_local_resolver()
        if _openshell_resolver is None:
            raise RuntimeError("OpenShell provider requires a trusted host admission resolver")
        if not run_config:
            return _UnboundOpenShellSandbox()
        from decepticon.backends.openshell_sandbox import OpenShellSandbox

        try:
            backend = _openshell_resolver(run_config)
        except RuntimeError as exc:
            # First-run journey (observed on the OSS verification stack):
            # graph construction runs before any engagement scope exists, and
            # the binding refuses to admit a workspace without a materialized
            # plan/roe.json. Booting must not crash-loop on that — an
            # unbound placeholder keeps the daemon up, the RoE guard fails
            # loudly if a run is attempted before /scope import, and the
            # binding resolves for real once the operator imports a scope.
            # A real run (has a thread_id) still fails loudly.
            if "materialized plan/roe.json" in str(exc) and not (
                isinstance(configurable, dict) and configurable.get("thread_id")
            ):
                return _UnboundOpenShellSandbox()
            raise
        if not isinstance(backend, OpenShellSandbox):
            raise RuntimeError("OpenShell resolver did not return an admitted OpenShell backend")
        backend.check_admission()
        return backend
    if host not in BUILTIN_PROVIDERS:
        backend = plugin_factory(host)(run_config)
        if backend is None:
            raise RuntimeError(f"sandbox provider {host!r} returned no backend")
        return backend
    base_url, token = _resolve_endpoint(config)
    return _shared_sandbox(base_url, token)
