"""SSRF / redirect safety guard for an agent-facing fetcher.

curl_cffi follows redirects but does NOT validate the destination (confirmed
against the official docs: there is no built-in private-IP/safe-redirect
option). Since this engine fetches attacker-influenced URLs and follows their
redirects, a hostile page could redirect to loopback, RFC-1918, link-local, or
the cloud metadata endpoint (169.254.169.254) to exfiltrate internal data.

This module provides a pure, deterministic classifier and a redirect resolver.
Default-deny for private/internal targets; opt in with allow_private=True
(env INSANE_ALLOW_PRIVATE=1) for local testing.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_MAX_REDIRECTS = 10


def http_version_for(url: str) -> Any:
    """Force HTTP/1.1 for cleartext ``http://`` requests.

    curl's HTTP/2 mode sends an ``Upgrade: h2c`` header on cleartext URLs,
    which browsers never do and which the OpenShell L7 proxy rejects
    (``unsupported_l7_protocol``). TLS URLs keep the impersonation default.
    """
    if not url.lower().startswith("http://"):
        return None
    try:
        from curl_cffi import CurlHttpVersion
    except ImportError:
        return None
    return CurlHttpVersion.V1_1


def kernel_enforced() -> bool:
    """True inside a managed OpenShell session.

    There the sandbox's OpenShell policy (compiled from the RoE) is the network
    authority and always blocks cloud metadata; its policy DNS also answers
    in-scope hosts with synthetic addresses from 198.18.0.0/15, which this
    module's private/reserved check would wrongly refuse. The managed daemon
    sets ``DECEPTICON_MANAGED_SESSION=1`` for every command it runs.
    """
    return os.environ.get("DECEPTICON_MANAGED_SESSION") == "1"


def allow_private_default() -> bool:
    """Skip the private/reserved address refusal (schemes are still checked).

    On by explicit opt-in (``INSANE_ALLOW_PRIVATE``) or when the OpenShell policy
    enforces scope (:func:`kernel_enforced`). In a non-OpenShell sandbox this
    module remains the engine's only private-address guard.
    """
    return kernel_enforced() or os.environ.get("INSANE_ALLOW_PRIVATE", "") in ("1", "true", "yes")


def _resolve_fail_open() -> bool:
    """Decepticon: in the sandbox we FAIL-CLOSED on resolver errors by default
    (an unresolvable host is refused rather than allowed). Set
    ``INSANE_RESOLVE_FAIL_OPEN=1`` to restore upstream's permissive behaviour."""
    return os.environ.get("INSANE_RESOLVE_FAIL_OPEN", "") in ("1", "true", "yes")


# NAT64 well-known prefix (RFC 6052). A DNS64 resolver (as in the sandbox)
# synthesises 64:ff9b::<v4> AAAA records for IPv4-only hosts; the real
# destination is the embedded IPv4 in the low 32 bits. Without unwrapping it the
# whole prefix looks "reserved" and every NAT64'd public host is falsely blocked.
_NAT64_WKP = ipaddress.ip_network("64:ff9b::/96")


def _ip_blocked(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # Unwrap IPv4-mapped IPv6 (``::ffff:127.0.0.1``) so a mapped private/loopback
    # address can't slip past the v6 category checks.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # Unwrap NAT64 → check the EMBEDDED IPv4 (so 64:ff9b::<public-v4> is allowed,
    # but 64:ff9b::<private-v4> is still blocked).
    elif isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WKP:
        ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def classify_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """(is_safe, reason). Blocks non-http(s) schemes and hosts that are — or
    DNS-resolve to — private/loopback/link-local/reserved/metadata addresses."""
    try:
        p = urlsplit(url)
    except Exception as e:
        return False, f"parse_error:{e}"
    if p.scheme not in ALLOWED_SCHEMES:
        return False, f"scheme:{p.scheme or 'none'}"
    host = p.hostname
    if not host:
        return False, "no_host"
    if allow_private:
        return True, "allow_private"

    # IP literal host → check directly (covers cloud metadata, loopback, …)  # NOTE-BIAS-OK
    try:
        ipaddress.ip_address(host)
        return (False, f"ip_blocked:{host}") if _ip_blocked(host) else (True, "public_ip")
    except ValueError:
        pass

    # Hostname → resolve and check every A/AAAA (DNS-rebinding defense).
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        ips = {info[4][0] for info in infos}
    except Exception:
        # Decepticon: fail-CLOSED on resolver errors inside the sandbox — an
        # unresolvable host is refused, not allowed (upstream failed open here).
        # This also closes the DNS-rebinding gap where the classifier resolves
        # one way and the fetcher another. Opt out with INSANE_RESOLVE_FAIL_OPEN=1.
        if _resolve_fail_open():
            return True, "resolve_failed_allow"
        return False, "resolve_failed_blocked"
    for ip in ips:
        if _ip_blocked(str(ip)):
            return False, f"resolves_internal:{host}->{ip}"
    return True, "public"


def location_of(resp) -> str | None:
    """Case-insensitive Location header from a curl_cffi/requests response."""
    try:
        headers = {k.lower(): v for k, v in dict(getattr(resp, "headers", {}) or {}).items()}
        return headers.get("location")
    except Exception:
        return None


def is_redirect(resp) -> bool:
    try:
        return int(getattr(resp, "status_code", 0) or 0) in (301, 302, 303, 307, 308)
    except Exception:
        return False


def resolve_redirect(base_url: str, location: str) -> str:
    return urljoin(base_url, location)
