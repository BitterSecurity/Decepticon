"""Subdomain takeover detection via dangling CNAME analysis.

The :func:`check_subdomain_takeover` tool resolves CNAME records for a
subdomain and matches them against a curated set of cloud/SaaS providers
known to allow subdomain takeover when the service behind the CNAME is
unclaimed.  When a CNAME matches, the tool makes an HTTP probe and checks
the response body for provider-specific error fingerprints.

The :func:`bulk_subdomain_takeover` tool wraps the single-target check for
batch processing of subdomain lists (JSON array or newline-separated).

Egress is gated on the engagement's Rules of Engagement: the target
subdomain is evaluated against ``plan/roe.json:machine_enforcement`` and,
in ``enforce`` mode, an out-of-scope / forbidden host is refused *before*
any socket is opened.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from decepticon_core.types.roe import (
    EnforcementMode,
    MachineEnforcement,
    evaluate_target,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("research.subdomain_takeover")

# ── Constants ───────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_BODY_SCAN_CHARS = 100_000

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ── Vulnerable service fingerprints ─────────────────────────────────────


def _probe_verify() -> bool | str:
    """TLS verification for active probes.

    Controlled via ``DECEPTICON_PROBE_VERIFY_TLS``. Defaults to
    ``true``. Set to ``false`` when scanning targets with self-signed
    or expired certificates.
    """
    val = os.environ.get("DECEPTICON_PROBE_VERIFY_TLS", "true").strip().lower()
    if val in ("0", "false", "no", ""):
        return False  # noqa: S501 — operator explicitly disabled via env var
    if val in ("1", "true", "yes"):
        return True
    return val


@dataclass(frozen=True, slots=True)
class _VulnerableService:
    """A cloud/SaaS provider susceptible to subdomain takeover."""

    service: str
    cnames: tuple[str, ...]
    fingerprint: str
    nxdomain: bool = False


VULNERABLE_SERVICES: tuple[_VulnerableService, ...] = (
    _VulnerableService(
        service="GitHub Pages",
        cnames=("*.github.io",),
        fingerprint="There isn't a GitHub Pages site here",
    ),
    _VulnerableService(
        service="Heroku",
        cnames=("*.herokuapp.com", "*.herokussl.com"),
        fingerprint="No such app",
    ),
    _VulnerableService(
        service="AWS S3",
        cnames=("*.s3.amazonaws.com", "*.s3-website"),
        fingerprint="NoSuchBucket",
    ),
    _VulnerableService(
        service="Shopify",
        cnames=("*.myshopify.com",),
        fingerprint="Sorry, this shop is currently unavailable",
    ),
    _VulnerableService(
        service="Fastly",
        cnames=("*.fastly.net",),
        fingerprint="Fastly error: unknown domain",
    ),
    _VulnerableService(
        service="Pantheon",
        cnames=("*.pantheonsite.io",),
        fingerprint="404 error unknown site",
    ),
    _VulnerableService(
        service="Tumblr",
        cnames=("*.tumblr.com",),
        fingerprint="There's nothing here",
    ),
    _VulnerableService(
        service="WordPress.com",
        cnames=("*.wordpress.com",),
        fingerprint="Do you want to register",
    ),
    _VulnerableService(
        service="Azure",
        cnames=(
            "*.azurewebsites.net",
            "*.cloudapp.azure.com",
            "*.trafficmanager.net",
            "*.blob.core.windows.net",
        ),
        fingerprint="404 Web Site not found",
        nxdomain=True,
    ),
    _VulnerableService(
        service="Surge.sh",
        cnames=("*.surge.sh",),
        fingerprint="project not found",
    ),
    _VulnerableService(
        service="Fly.io",
        cnames=("*.fly.dev",),
        fingerprint="not found",
        nxdomain=True,
    ),
    _VulnerableService(
        service="Netlify",
        cnames=("*.netlify.app", "*.netlify.com"),
        fingerprint="Not Found - Request ID",
    ),
    _VulnerableService(
        service="Cargo Collective",
        cnames=("*.cargocollective.com",),
        fingerprint="404 Not Found",
    ),
    _VulnerableService(
        service="Ghost",
        cnames=("*.ghost.io",),
        fingerprint="The thing you were looking for is no longer here",
    ),
    _VulnerableService(
        service="Unbounce",
        cnames=("*.unbouncepages.com",),
        fingerprint="The requested URL was not found on this server",
    ),
    _VulnerableService(
        service="Zendesk",
        cnames=("*.zendesk.com",),
        fingerprint="Help Center Closed",
    ),
    _VulnerableService(
        service="Bitbucket",
        cnames=("*.bitbucket.io",),
        fingerprint="Repository not found",
    ),
    _VulnerableService(
        service="UserVoice",
        cnames=("*.uservoice.com",),
        fingerprint="This UserVoice subdomain is currently available",
    ),
    _VulnerableService(
        service="Feedpress",
        cnames=("redirect.feedpress.me",),
        fingerprint="The feed has not been found",
    ),
    _VulnerableService(
        service="Agile CRM",
        cnames=("*.agilecrm.com",),
        fingerprint="Sorry, this page is no longer available",
    ),
    _VulnerableService(
        service="Tilda",
        cnames=("*.tilda.ws",),
        fingerprint="Please renew your subscription",
    ),
    _VulnerableService(
        service="Smartling",
        cnames=("*.smartling.com",),
        fingerprint="Domain is not configured",
    ),
    _VulnerableService(
        service="Readme.io",
        cnames=("*.readme.io",),
        fingerprint="Project doesnt exist",
    ),
    _VulnerableService(
        service="HubSpot",
        cnames=("*.hs-sites.com", "*.hubspot.net"),
        fingerprint="Domain not found",
    ),
)


# ── Helpers ─────────────────────────────────────────────────────────────


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


def _load_roe_rules() -> MachineEnforcement:
    """Load the engagement's machine-enforcement RoE block.

    Falls back to a permissive default (mode=``log``) when there is no
    plan directory or the file cannot be parsed.
    """
    plan_root = Path(os.getenv("DECEPTICON_PLAN_DIR", "plan"))
    roe_path = plan_root / "roe.json"
    if not roe_path.is_file():
        return MachineEnforcement.from_dict({"mode": "log", "scope": []})
    try:
        block = json.loads(roe_path.read_text()).get("machine_enforcement", {})
    except Exception:
        return MachineEnforcement.from_dict({"mode": "log", "scope": []})
    return MachineEnforcement.from_dict(block)


def _resolve_cname(subdomain: str) -> list[str]:
    """Resolve CNAME records for *subdomain* using ``dig``, falling back to
    ``socket.getfqdn`` if ``dig`` is unavailable."""
    cnames: list[str] = []
    try:
        result = subprocess.run(
            ["dig", "+short", "CNAME", subdomain],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip().rstrip(".")
            if line:
                cnames.append(line.lower())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # dig not available — try socket as a rough fallback
        try:
            fqdn = socket.getfqdn(subdomain)
            if fqdn and fqdn.lower() != subdomain.lower():
                cnames.append(fqdn.lower())
        except OSError:
            pass
    return cnames


def _check_nxdomain(subdomain: str) -> bool:
    """Return True if the subdomain fails DNS A/AAAA resolution (NXDOMAIN)."""
    try:
        socket.getaddrinfo(subdomain, None)
        return False
    except socket.gaierror:
        return True


def _match_cname(cname: str, pattern: str) -> bool:
    """Check whether *cname* matches a wildcard pattern like ``*.github.io``."""
    if pattern.startswith("*."):
        suffix = pattern[1:]  # e.g. ".github.io"
        return cname.endswith(suffix) or cname == suffix[1:]
    return cname == pattern or cname.endswith("." + pattern)


def _find_service(cnames: list[str]) -> _VulnerableService | None:
    """Return the first matching vulnerable service for a list of CNAMEs."""
    for cname in cnames:
        for svc in VULNERABLE_SERVICES:
            for pattern in svc.cnames:
                if _match_cname(cname, pattern):
                    return svc
    return None


async def _http_fingerprint(subdomain: str, fingerprint: str) -> dict[str, Any]:
    """Probe the subdomain over HTTP(S) and check for the error fingerprint."""
    result: dict[str, Any] = {
        "http_reachable": False,
        "fingerprint_match": False,
        "status_code": None,
        "error": None,
    }
    for scheme in ("https", "http"):
        url = f"{scheme}://{subdomain}/"
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                headers={"User-Agent": _USER_AGENT},
                verify=_probe_verify(),
            ) as client:
                resp = await client.get(url)
            result["http_reachable"] = True
            result["status_code"] = resp.status_code
            body = resp.text[:MAX_BODY_SCAN_CHARS]
            if fingerprint.lower() in body.lower():
                result["fingerprint_match"] = True
            return result
        except httpx.HTTPError:
            continue
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
            continue
    return result


def _compute_confidence(
    service_match: bool,
    nxdomain: bool,
    fingerprint_match: bool,
    http_error: bool,
    svc_nxdomain_flag: bool,
) -> str:
    """Compute a confidence level for the takeover assessment.

    - ``high``:   CNAME matches + fingerprint confirmed in HTTP response
    - ``medium``: CNAME matches + (NXDOMAIN on service or HTTP error/404)
    - ``low``:    CNAME matches a known vulnerable service only
    - ``none``:   no CNAME match
    """
    if not service_match:
        return "none"
    if fingerprint_match:
        return "high"
    if nxdomain or http_error:
        return "medium"
    return "low"


# ── Tools ───────────────────────────────────────────────────────────────


@tool
async def check_subdomain_takeover(subdomain: str) -> str:
    """Check a single subdomain for potential subdomain takeover.

    WHEN TO USE: During recon, after subdomain enumeration.  Run this on
    discovered subdomains to identify dangling CNAMEs pointing at unclaimed
    cloud / SaaS resources.  A successful match (confidence ``high``)
    indicates the subdomain can likely be claimed by registering the target
    service resource.

    Performs DNS CNAME resolution, matches against known vulnerable services,
    then makes a single HTTP probe to confirm the provider error fingerprint.
    Egress is gated on the engagement RoE — an out-of-scope or forbidden host
    is refused before any request when the RoE is in ``enforce`` mode.

    Args:
        subdomain: Fully qualified subdomain to check (e.g. ``staging.example.com``).

    Returns:
        JSON string with ``cnames``, ``service_match``, ``fingerprint_match``,
        ``takeover_possible``, ``confidence``, and ``details``.
    """
    subdomain = subdomain.strip().lower()
    if not subdomain:
        return _json({"error": "empty subdomain", "subdomain": subdomain})

    # ── RoE gating ──────────────────────────────────────────────────────
    rules = _load_roe_rules()
    decision = evaluate_target(subdomain, rules)
    if not decision.allow and rules.mode == EnforcementMode.ENFORCE:
        log.warning("subdomain_takeover: RoE refused %s (%s)", subdomain, decision.reason_code)
        return _json(
            {
                "subdomain": subdomain,
                "scope": "refused",
                "reason_code": decision.reason_code,
                "reason_detail": decision.reason_detail,
                "error": f"RoE refused egress to {subdomain!r}: {decision.reason_detail}",
            }
        )

    # ── DNS CNAME resolution ────────────────────────────────────────────
    cnames = _resolve_cname(subdomain)
    nxdomain = _check_nxdomain(subdomain)
    service = _find_service(cnames)

    result: dict[str, Any] = {
        "subdomain": subdomain,
        "scope": "allowed",
        "cnames": cnames,
        "dangling_cname": bool(cnames) and nxdomain,
        "nxdomain": nxdomain,
        "service_match": None,
        "fingerprint_match": False,
        "takeover_possible": False,
        "confidence": "none",
        "details": None,
    }

    if not cnames:
        result["details"] = "No CNAME records found; not a takeover candidate."
        return _json(result)

    if service is None:
        result["details"] = f"CNAME(s) {cnames} do not match any known vulnerable service."
        return _json(result)

    result["service_match"] = service.service

    # ── HTTP fingerprint probe ──────────────────────────────────────────
    probe = await _http_fingerprint(subdomain, service.fingerprint)
    result["fingerprint_match"] = probe["fingerprint_match"]
    result["http_status"] = probe["status_code"]

    http_error = not probe["http_reachable"] or (
        probe["status_code"] is not None and probe["status_code"] >= 400
    )

    confidence = _compute_confidence(
        service_match=True,
        nxdomain=nxdomain,
        fingerprint_match=probe["fingerprint_match"],
        http_error=http_error,
        svc_nxdomain_flag=service.nxdomain,
    )
    result["confidence"] = confidence
    result["takeover_possible"] = confidence in ("high", "medium")

    if confidence == "high":
        result["details"] = (
            f"CNAME points to {service.service} and the error fingerprint "
            f"({service.fingerprint!r}) was confirmed. Takeover highly likely."
        )
    elif confidence == "medium":
        result["details"] = (
            f"CNAME points to {service.service}. "
            f"{'NXDOMAIN detected' if nxdomain else 'HTTP error received'} "
            f"but fingerprint not confirmed. Manual verification recommended."
        )
    else:
        result["details"] = (
            f"CNAME points to {service.service} but the resource appears "
            f"active (HTTP {probe['status_code']}). Low takeover likelihood."
        )

    return _json(result)


@tool
async def bulk_subdomain_takeover(subdomains_json: str) -> str:
    """Check multiple subdomains for potential subdomain takeover.

    WHEN TO USE: After subdomain enumeration (e.g. from subfinder output),
    batch-check a list of subdomains for dangling CNAME takeover opportunities.

    Accepts either a JSON array of strings or a newline-separated list.
    Runs :func:`check_subdomain_takeover` for each entry and returns an
    aggregated summary.

    Args:
        subdomains_json: JSON array ``["sub1.example.com", ...]`` **or**
            newline-separated subdomain list.

    Returns:
        JSON string with ``total``, ``vulnerable`` (count with confidence
        ≥ medium), and a ``results`` list of individual check results.
    """
    # ── Parse input ─────────────────────────────────────────────────────
    subdomains: list[str] = []
    raw = subdomains_json.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                subdomains = [str(s).strip() for s in parsed if str(s).strip()]
        except json.JSONDecodeError:
            return _json({"error": "invalid JSON array", "input": raw[:200]})
    else:
        subdomains = [s.strip() for s in raw.splitlines() if s.strip()]

    if not subdomains:
        return _json({"error": "no subdomains provided", "total": 0})

    # ── Run checks concurrently ─────────────────────────────────────────
    tasks = [check_subdomain_takeover.ainvoke(sd) for sd in subdomains]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[dict[str, Any]] = []
    for sd, raw_res in zip(subdomains, raw_results):
        if isinstance(raw_res, Exception):
            results.append({"subdomain": sd, "error": f"{type(raw_res).__name__}: {raw_res}"})
        else:
            try:
                results.append(json.loads(raw_res))
            except (json.JSONDecodeError, TypeError):
                results.append({"subdomain": sd, "raw": str(raw_res)})

    vulnerable = [r for r in results if r.get("confidence") in ("high", "medium")]

    return _json(
        {
            "total": len(results),
            "vulnerable": len(vulnerable),
            "results": results,
        }
    )


# ── Public tool list ────────────────────────────────────────────────────

SUBDOMAIN_TAKEOVER_TOOLS = [check_subdomain_takeover, bulk_subdomain_takeover]
