"""OSINT enrichment — passive host/domain intelligence.

Aggregates open-port, certificate, DNS, and service-banner intelligence
for a target domain/host from passive OSINT data sources (Shodan, Censys,
ZoomEye). The enrichment is *passive*: it never touches the target — it
only queries third-party indexes that already hold scan data.

Operating modes
---------------
- **Live**: a source is queried over its public API when its credentials
  are present in the environment (``SHODAN_API_KEY``,
  ``CENSYS_API_ID`` + ``CENSYS_API_SECRET``, ``ZOOMEYE_API_KEY``).
- **Mock**: when *no* source credentials are configured the tool falls
  back to a local mock catalog so the agent can develop and test offline.
  The catalog is deterministic (derived from the domain) and may be
  overridden with a JSON file via ``DECEPTICON_OSINT_CATALOG``.

Scope safety
------------
Every call is logged and the target is checked against the engagement's
target-scope rules (``DECEPTICON_OSINT_SCOPE`` — a comma-separated list of
host / ``*.wildcard`` patterns). When a scope is configured and the target
does not match it, the lookup is refused before any network egress. An
empty scope means "no scoping configured" and is allowed (consistent with
``get_active_engagement``), but the call is still logged.

``httpx.AsyncClient`` is used for all egress so lookups stay non-blocking
and are trivially mockable via ``httpx.MockTransport`` in tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from decepticon.tools.research._state import _json
from decepticon_core.types.roe import matches_domain_glob
from decepticon_core.utils.logging import get_logger

log = get_logger("research.osint")

# ── Endpoints ───────────────────────────────────────────────────────────

SHODAN_SEARCH_URL = "https://api.shodan.io/shodan/host/search"
SHODAN_DNS_URL = "https://api.shodan.io/dns/domain/{domain}"
CENSYS_SEARCH_URL = "https://search.censys.io/api/v2/hosts/search"
ZOOMEYE_SEARCH_URL = "https://api.zoomeye.org/host/search"

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


# ── Scope rules ─────────────────────────────────────────────────────────


def _normalize_domain(domain: str) -> str:
    """Strip scheme, path, port, and surrounding whitespace from ``domain``."""
    target = (domain or "").strip().lower()
    if "://" in target:
        target = target.split("://", 1)[1]
    target = target.split("/", 1)[0]
    # Drop a trailing :port but keep bare IPv6 brackets intact.
    if target.count(":") == 1:
        target = target.split(":", 1)[0]
    return target.strip(".")


def _load_scope_patterns() -> list[str]:
    """Return the configured target-scope patterns, lowercased.

    Read from ``DECEPTICON_OSINT_SCOPE`` (comma-separated). An empty /
    unset value yields ``[]`` ("no scoping configured").
    """
    raw = os.environ.get("DECEPTICON_OSINT_SCOPE", "")
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _matches_pattern(target: str, pattern: str) -> bool:
    """Match ``target`` against a single host / ``*.wildcard`` pattern."""
    return matches_domain_glob(pattern, target)


def _is_in_scope(target: str, patterns: list[str]) -> bool:
    """Return True if ``target`` is allowed. Empty scope allows everything."""
    if not patterns:
        return True
    return any(_matches_pattern(target, p) for p in patterns)


# ── Source availability ─────────────────────────────────────────────────


def _available_sources() -> list[str]:
    """Return the OSINT sources with credentials present in the environment."""
    sources: list[str] = []
    if os.environ.get("SHODAN_API_KEY"):
        sources.append("shodan")
    if os.environ.get("CENSYS_API_ID") and os.environ.get("CENSYS_API_SECRET"):
        sources.append("censys")
    if os.environ.get("ZOOMEYE_API_KEY"):
        sources.append("zoomeye")
    return sources


# ── Findings model ──────────────────────────────────────────────────────

_FINDING_KEYS = ("open_ports", "certificates", "dns_records", "banners")


def _new_findings() -> dict[str, list[Any]]:
    return {key: [] for key in _FINDING_KEYS}


def _dedupe(items: list[Any]) -> list[Any]:
    """Order-preserving dedupe that tolerates dict (unhashable) elements."""
    seen: set[str] = set()
    out: list[Any] = []
    for item in items:
        marker = json.dumps(item, sort_keys=True, default=str)
        if marker not in seen:
            seen.add(marker)
            out.append(item)
    return out


def _merge_findings(dst: dict[str, list[Any]], src: dict[str, list[Any]]) -> None:
    """Merge ``src`` finding lists into ``dst``, deduping each category."""
    for key in _FINDING_KEYS:
        if src.get(key):
            dst[key] = _dedupe(dst[key] + list(src[key]))


# ── Parsers (pure) ──────────────────────────────────────────────────────


def _parse_shodan(host_data: dict[str, Any], dns_data: dict[str, Any]) -> dict[str, list[Any]]:
    """Normalize a Shodan host-search + DNS response into findings."""
    out = _new_findings()
    for match in host_data.get("matches", []):
        port = match.get("port")
        ip = match.get("ip_str") or match.get("ip")
        service = (match.get("_shodan") or {}).get("module") or match.get("product")
        if port is not None:
            out["open_ports"].append({"port": port, "transport": match.get("transport", "tcp")})
        banner = match.get("data")
        if banner:
            out["banners"].append(
                {"ip": ip, "port": port, "service": service, "banner": banner.strip()}
            )
        ssl = match.get("ssl") or {}
        cert = ssl.get("cert") or {}
        if cert:
            out["certificates"].append(
                {
                    "subject_cn": (cert.get("subject") or {}).get("CN"),
                    "issuer_cn": (cert.get("issuer") or {}).get("CN"),
                    "expires": cert.get("expires"),
                    "fingerprint_sha256": (cert.get("fingerprint") or {}).get("sha256"),
                }
            )
    for rec in dns_data.get("data", []):
        out["dns_records"].append(
            {
                "type": rec.get("type"),
                "name": rec.get("subdomain") or dns_data.get("domain"),
                "value": rec.get("value"),
            }
        )
    return out


def _parse_censys(data: dict[str, Any]) -> dict[str, list[Any]]:
    """Normalize a Censys hosts-search v2 response into findings."""
    out = _new_findings()
    for hit in (data.get("result") or {}).get("hits", []):
        ip = hit.get("ip")
        for svc in hit.get("services", []):
            port = svc.get("port")
            if port is not None:
                out["open_ports"].append(
                    {"port": port, "transport": svc.get("transport_protocol", "TCP").lower()}
                )
            banner = svc.get("banner")
            if banner:
                out["banners"].append(
                    {
                        "ip": ip,
                        "port": port,
                        "service": svc.get("service_name"),
                        "banner": banner.strip(),
                    }
                )
            tls = svc.get("tls") or {}
            cert = (tls.get("certificates") or {}).get("leaf_data") or {}
            if cert:
                subject = cert.get("subject") or {}
                issuer = cert.get("issuer") or {}
                out["certificates"].append(
                    {
                        "subject_cn": (subject.get("common_name") or [None])[0]
                        if isinstance(subject.get("common_name"), list)
                        else subject.get("common_name"),
                        "issuer_cn": (issuer.get("common_name") or [None])[0]
                        if isinstance(issuer.get("common_name"), list)
                        else issuer.get("common_name"),
                        "fingerprint_sha256": cert.get("fingerprint_sha256"),
                    }
                )
        for name in (hit.get("dns") or {}).get("names", []):
            out["dns_records"].append({"type": "A", "name": name, "value": ip})
    return out


def _parse_zoomeye(data: dict[str, Any]) -> dict[str, list[Any]]:
    """Normalize a ZoomEye host-search response into findings."""
    out = _new_findings()
    for match in data.get("matches", []):
        portinfo = match.get("portinfo") or {}
        port = portinfo.get("port")
        ip = match.get("ip")
        if port is not None:
            out["open_ports"].append({"port": port, "transport": "tcp"})
        banner = portinfo.get("banner")
        if banner:
            out["banners"].append(
                {
                    "ip": ip,
                    "port": port,
                    "service": portinfo.get("service"),
                    "banner": banner.strip(),
                }
            )
        for rdns in match.get("rdns", []) if isinstance(match.get("rdns"), list) else []:
            out["dns_records"].append({"type": "PTR", "name": rdns, "value": ip})
    return out


# ── Fetchers (mockable egress) ──────────────────────────────────────────


async def _fetch_shodan(
    client: httpx.AsyncClient, domain: str, api_key: str
) -> dict[str, list[Any]]:
    host_resp = await client.get(
        SHODAN_SEARCH_URL, params={"key": api_key, "query": f"hostname:{domain}"}
    )
    host_resp.raise_for_status()
    dns_resp = await client.get(SHODAN_DNS_URL.format(domain=domain), params={"key": api_key})
    dns_resp.raise_for_status()
    return _parse_shodan(host_resp.json(), dns_resp.json())


async def _fetch_censys(
    client: httpx.AsyncClient, domain: str, api_id: str, api_secret: str
) -> dict[str, list[Any]]:
    resp = await client.get(
        CENSYS_SEARCH_URL,
        params={"q": domain, "per_page": 25},
        auth=(api_id, api_secret),
    )
    resp.raise_for_status()
    return _parse_censys(resp.json())


async def _fetch_zoomeye(
    client: httpx.AsyncClient, domain: str, api_key: str
) -> dict[str, list[Any]]:
    resp = await client.get(
        ZOOMEYE_SEARCH_URL,
        params={"query": f"hostname:{domain}"},
        headers={"API-KEY": api_key},
    )
    resp.raise_for_status()
    return _parse_zoomeye(resp.json())


# ── Local mock catalog ──────────────────────────────────────────────────


def _load_mock_catalog(domain: str) -> dict[str, list[Any]]:
    """Return mock findings for ``domain``.

    If ``DECEPTICON_OSINT_CATALOG`` points at a JSON file, look the domain
    up there (the file maps ``domain -> findings``); otherwise synthesize a
    deterministic catalog from the domain so output is stable across runs.
    """
    catalog_path = os.environ.get("DECEPTICON_OSINT_CATALOG", "").strip()
    if catalog_path:
        try:
            catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
            entry = catalog.get(domain) or catalog.get("*")
            if entry:
                merged = _new_findings()
                _merge_findings(merged, entry)
                return merged
        except (OSError, json.JSONDecodeError, AttributeError) as e:
            log.warning("osint mock catalog %s unusable: %s", catalog_path, e)

    digest = hashlib.sha256(domain.encode("utf-8")).hexdigest()
    return {
        "open_ports": [
            {"port": 22, "transport": "tcp"},
            {"port": 80, "transport": "tcp"},
            {"port": 443, "transport": "tcp"},
        ],
        "certificates": [
            {
                "subject_cn": domain,
                "issuer_cn": "Mock CA",
                "expires": "2030-01-01T00:00:00",
                "fingerprint_sha256": digest,
            }
        ],
        "dns_records": [
            {"type": "A", "name": domain, "value": "192.0.2.1"},
            {"type": "MX", "name": domain, "value": f"mail.{domain}"},
        ],
        "banners": [
            {"ip": "192.0.2.1", "port": 22, "service": "ssh", "banner": "SSH-2.0-OpenSSH_8.9"},
            {
                "ip": "192.0.2.1",
                "port": 80,
                "service": "http",
                "banner": "HTTP/1.1 200 OK\r\nServer: nginx",
            },
        ],
    }


# ── Tool ────────────────────────────────────────────────────────────────


@tool
async def osint_enrich(domain: str) -> str:
    """Enrich a domain/host with passive OSINT (ports, certs, DNS, banners).

    WHEN TO USE: During passive reconnaissance, before any active scanning,
    to learn what third-party indexes (Shodan / Censys / ZoomEye) already
    know about a target — open ports, TLS certificates, DNS records, and
    service banners. The lookup is passive: it never touches the target.

    Live sources are queried only when their credentials are present
    (``SHODAN_API_KEY``, ``CENSYS_API_ID`` + ``CENSYS_API_SECRET``,
    ``ZOOMEYE_API_KEY``). With no credentials configured the tool returns a
    deterministic local mock catalog so it stays usable offline.

    The target is checked against the engagement target-scope rules
    (``DECEPTICON_OSINT_SCOPE``); an out-of-scope target is refused before
    any network egress. Every call is logged.

    Args:
        domain: The target domain or host (scheme/path/port are stripped),
            e.g. ``"example.com"`` or ``"https://api.example.com:443/"``.

    Returns:
        JSON object with ``domain``, ``in_scope``, ``sources`` (the sources
        consulted, ``["mock"]`` offline), ``open_ports``, ``certificates``,
        ``dns_records``, ``banners``, and an ``errors`` list when a live
        source fails.
    """
    target = _normalize_domain(domain)
    if not target:
        return _json({"error": "no domain provided"})

    scope = _load_scope_patterns()
    in_scope = _is_in_scope(target, scope)
    log.info("osint_enrich target=%r scope_patterns=%d in_scope=%s", target, len(scope), in_scope)
    if not in_scope:
        log.warning("osint_enrich refused: %r is out of target scope %s", target, scope)
        return _json(
            {
                "domain": target,
                "in_scope": False,
                "error": f"target {target!r} is out of scope",
                "scope_patterns": scope,
            }
        )

    findings = _new_findings()
    errors: list[str] = []
    sources = _available_sources()
    used: list[str] = []

    if sources:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            for name in sources:
                try:
                    if name == "shodan":
                        part = await _fetch_shodan(client, target, os.environ["SHODAN_API_KEY"])
                    elif name == "censys":
                        part = await _fetch_censys(
                            client,
                            target,
                            os.environ["CENSYS_API_ID"],
                            os.environ["CENSYS_API_SECRET"],
                        )
                    else:  # zoomeye
                        part = await _fetch_zoomeye(client, target, os.environ["ZOOMEYE_API_KEY"])
                    _merge_findings(findings, part)
                    used.append(name)
                    log.info("osint_enrich %s ok for %r", name, target)
                except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as e:
                    log.warning("osint_enrich %s failed for %r: %s", name, target, e)
                    errors.append(f"{name}: {e}")

    if not used:
        _merge_findings(findings, _load_mock_catalog(target))
        used.append("mock")
        log.info("osint_enrich using mock catalog for %r (no live sources)", target)

    result: dict[str, Any] = {"domain": target, "in_scope": True, "sources": used, **findings}
    if errors:
        result["errors"] = errors
    return _json(result)


OSINT_TOOLS = [osint_enrich]
