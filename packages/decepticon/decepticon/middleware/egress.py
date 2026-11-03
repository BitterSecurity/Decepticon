"""OSINT egress hosts shared by the RoE boundary compilers.

``web_search`` is OSINT (target-exempt at the RoE tool layer — see
``decepticon.middleware.roe``), so its provider hosts must stay reachable
under an enforcing in-scope allowlist or the tool would silently fail. The
OpenShell kernel policy compiler (``decepticon.middleware.policy_compiler``)
renders them as a separate allowance section of the sandbox policy.

Mirrors the provider allowlist in ``decepticon.sandbox_web.providers``
(kept as a small literal here to avoid the middleware importing the
in-sandbox engine). Extend/replace via ``DECEPTICON_OSINT_EGRESS_HOSTS``
(comma-separated); set it empty to disable.
"""

from __future__ import annotations

import os

_DEFAULT_OSINT_EGRESS_HOSTS = ("duckduckgo.com", "html.duckduckgo.com")


def osint_egress_hosts() -> tuple[str, ...]:
    raw = os.environ.get("DECEPTICON_OSINT_EGRESS_HOSTS")
    if raw is None:
        return _DEFAULT_OSINT_EGRESS_HOSTS
    return tuple(h.strip() for h in raw.split(",") if h.strip())


__all__ = ["osint_egress_hosts"]
