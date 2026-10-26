"""Passive in-scope discovery for engagement creation.

When an operator authorizes a root domain, enrich the engagement scope with the
subdomains that already exist in **public Certificate Transparency logs**. This
is passive OSINT — it queries crt.sh, never the target — so it widens the
*inventory* without touching the authorized host. Discovery is best-effort:
any network/parse failure degrades to the operator-provided targets.

Safety: discovered hosts are filtered to the authorized root (only names ending
in ``.<root>`` or equal to it), so CT noise / shared-cert third parties can
never silently widen scope beyond what the operator authorized.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import urllib.request
from collections.abc import Callable
from urllib.parse import quote

from decepticon_core.types.roe import (
    matches_domain_glob,
    normalize_scope_target,
    split_scope_targets,
    wildcard_base_domain,
)

log = logging.getLogger(__name__)

# Bounds so a popular root (thousands of CT entries) can't blow up creation.
_MAX_DISCOVERED = 500
_DEFAULT_TIMEOUT = 15.0

Fetcher = Callable[[str], str]


def _crtsh_fetch(url: str, timeout: float = _DEFAULT_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "decepticon-roe/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return resp.read().decode("utf-8", "replace")


def _is_plain_hostname(target: str) -> bool:
    """A bare hostname eligible for subdomain discovery (not URL/CIDR/IP/glob)."""
    if not target or "*" in target or "?" in target or "/" in target or "://" in target:
        return False
    try:
        ipaddress.ip_address(target)
        return False
    except ValueError:
        return "." in target


def discover_subdomains(
    root: str,
    *,
    fetch: Fetcher | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[str]:
    """Return CT-log subdomains of ``root`` (deduped, filtered to the root).

    Best-effort: returns ``[]`` on any failure. ``fetch`` is injectable so the
    default crt.sh HTTP call can be stubbed in tests.
    """
    root = (root or "").strip().lower().rstrip(".")
    if not _is_plain_hostname(root):
        return []
    fetch = fetch or _crtsh_fetch
    url = f"https://crt.sh/?q={quote('%.' + root)}&output=json"
    try:
        body = fetch(url)
        entries = json.loads(body)
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        log.warning("passive discovery failed for %s: %s", root, exc)
        return []
    suffix = "." + root
    found: dict[str, None] = {}
    for entry in entries if isinstance(entries, list) else []:
        name_value = entry.get("name_value", "") if isinstance(entry, dict) else ""
        for name in str(name_value).splitlines():
            host = name.strip().lstrip("*.").lower().rstrip(".")
            if not host or "*" in host:
                continue
            if host == root or host.endswith(suffix):
                found.setdefault(host, None)
            if len(found) >= _MAX_DISCOVERED:
                return list(found)
    return list(found)


def expand_targets(
    targets: list[str],
    *,
    fetch: Fetcher | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> list[str]:
    """Return ``targets`` plus CT-discovered subdomains for each hostname target.

    Plain hostnames, URLs, and git remotes drive discovery against their
    normalized host; ``*.`` subdomain globs drive it against their base domain
    (the glob itself stays in scope). Embedded lists (``"a, b"``) split into
    their targets first. Order-preserving and deduped; the original targets
    always come first so an operator-authored entry is never dropped by
    discovery.
    """
    out: dict[str, None] = {t: None for t in split_scope_targets(targets)}
    for target in list(out):
        candidate = normalize_scope_target(target)
        root = candidate if _is_plain_hostname(candidate) else wildcard_base_domain(candidate)
        if root:
            for host in discover_subdomains(root, fetch=fetch, timeout=timeout):
                if wildcard_base_domain(candidate) and not matches_domain_glob(candidate, host):
                    continue
                out.setdefault(host, None)
    return list(out)
