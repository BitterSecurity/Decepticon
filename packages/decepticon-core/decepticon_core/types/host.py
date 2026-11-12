"""Per-run configuration a trusted host passes to the framework.

A host (the OSS CLI and web clients, a desktop host, or a downstream product
that runs the framework as a library) launches each LangGraph run with a
``config["configurable"]`` mapping. This module declares the keys the
framework actually reads from that mapping. It is the typed half of the host
integration contract; the callable half is ``decepticon_sdk.host``.

Every key is optional. Values are trusted inputs: a host that accepts them from
an end user must authenticate and validate them before forwarding them here.
Nothing in this mapping grants authorization by itself — scope enforcement,
tool restrictions and human approval remain runtime contracts.

Keys the framework does not read are ignored. Adding a key here is a contract
change and must be paired with the code that reads it. A resolver installed with
``configure_openshell_resolver`` receives the whole run config; any keys it reads
(for example a binding reference) are that resolver's own contract, not this one.

Stdlib-only, so it can be imported from any context.
"""

from __future__ import annotations

from typing import TypedDict


class HostRunConfigurable(TypedDict, total=False):
    """Keys of ``config["configurable"]`` the framework reads on each run."""

    # Engagement binding — hydrated into agent state by the engagement context
    # middleware, then carried by the checkpointer. When the launcher pins a
    # runtime engagement (``DECEPTICON_RUNTIME_ENGAGEMENT``), a run bound to a
    # different engagement is refused.
    engagement_name: str
    """Engagement slug; also the knowledge-graph label when ``kg_engagement`` is absent."""
    kg_engagement: str
    """Authoritative knowledge-graph partition (e.g. a composite tenant+engagement
    label). Re-applied on every run, so it overrides a stale checkpointed value."""
    workspace_path: str
    """Absolute engagement workspace path inside the sandbox (e.g. ``/workspace/<slug>``)."""
    language: str
    """ISO-639-1 response language for operator-facing output."""
    target_type: str
    """Kind of the launcher-declared target. ``"direct_scope"`` marks it as a display
    anchor for a provisioned ``plan/roe.json``; it never expands scope."""
    target_value: str
    """The launcher-declared target, shown to the agent as exactly one target."""
    authorization_confirmed: bool
    """Current trusted launch context: whether the operator confirmed authorization.
    Re-read on every run (approval can be granted or withdrawn after a checkpoint);
    a missing or non-boolean value never grants access."""

    # Sandbox routing — read by the backend factory and the bash tool so a
    # shared process can route each run to its own engagement sandbox. The
    # host's provider comes from ``DECEPTICON_SANDBOX_PROVIDER`` (default
    # ``"openshell"``; ``"docker"`` — formerly ``"http"`` — is the plain sandbox
    # daemon without a network boundary; plugins may register more).
    sandbox_url: str
    """Base URL of this run's sandbox daemon. Used only by the ``docker`` provider;
    an OpenShell host refuses runs that pass it."""
    sandbox_token: str
    """Bearer token for ``sandbox_url``. Used only on the HTTP transport."""
    sandbox_provider: str
    """``"openshell"`` or ``"docker"`` (``"http"`` is a deprecated alias). A run on a
    non-OpenShell host may request
    ``"openshell"``, which requires a trusted resolver installed with
    ``configure_openshell_resolver``. On an OpenShell host any other value is
    refused — a run can never downgrade the host's provider."""

    # Trusted workflow selection — only server-side configuration selects a
    # catalog entry; the runtime pins the selected source in the checkpoint.
    workflow_id: str
    """Registered execution workflow ID."""
    workflow_version: str
    """Exact execution workflow version; requires ``workflow_id``."""
    planning_workflow_id: str
    """Registered planning workflow ID (optional Plan mode)."""
    planning_workflow_version: str
    """Exact planning workflow version; requires ``planning_workflow_id``."""


HOST_RUN_CONFIGURABLE_KEYS: frozenset[str] = frozenset(HostRunConfigurable.__annotations__)
"""Every key declared by :class:`HostRunConfigurable`."""


__all__ = ["HOST_RUN_CONFIGURABLE_KEYS", "HostRunConfigurable"]
