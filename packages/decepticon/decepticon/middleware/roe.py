"""Deprecated: the RoE guardrail middleware was removed.

Network scope is enforced only by the OpenShell sandbox policy compiled from
``plan/roe.json`` (``middleware/policy_compiler.py``). Execution time windows
are enforced by :class:`decepticon.middleware.time_window.TimeWindowMiddleware`.
The application-layer target/command checks, request pacing, concurrency
limits, ``.abort`` marker and the RoE decision ledger no longer exist.

This module keeps the previous public names importable for one release so
downstream plugins built against it still load. ``RoEGuardrailMiddleware`` is
a pass-through that enforces nothing; ``NETWORK_TARGET_EXTRACTORS`` and
``GATED_TOOL_NAMES`` are inert. Migrate to the OpenShell policy and
``TimeWindowMiddleware``.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from langchain.agents.middleware import AgentMiddleware

from decepticon.middleware._roe_rules import load_machine_enforcement
from decepticon_core.types.roe import MachineEnforcement

warnings.warn(
    "decepticon.middleware.roe is deprecated and enforces nothing; network scope is "
    "enforced by the OpenShell policy and time windows by TimeWindowMiddleware",
    DeprecationWarning,
    stacklevel=2,
)

#: Inert since the removal; retained for import compatibility.
GATED_TOOL_NAMES: frozenset[str] = frozenset()

#: Inert since the removal; plugins may still register into it.
NETWORK_TARGET_EXTRACTORS: dict[str, Callable[[dict[str, Any]], list[str]]] = {}


def _host_from_url(url: Any) -> list[str]:
    if not isinstance(url, str) or not url:
        return []
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return []
    return [host] if host else []


def _hosts_from_url_arg(args: dict[str, Any]) -> list[str]:
    return _host_from_url(args.get("url"))


def _load_rules_for_workspace(workspace_path: str | None) -> MachineEnforcement:
    return load_machine_enforcement(workspace_path)


def build_default_sink(workspace_path: str | None) -> None:
    """The RoE decision ledger was removed; always ``None``."""
    return None


class RoEGuardrailMiddleware(AgentMiddleware):
    """Deprecated pass-through; accepts the previous constructor arguments."""

    def __init__(
        self,
        *,
        sink: Any = None,
        gated_tools: frozenset[str] | None = None,
        **_: Any,
    ) -> None:
        super().__init__()


RoEEnforcementMiddleware = RoEGuardrailMiddleware
