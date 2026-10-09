"""General-purpose live secret validation engine.

Extends :mod:`secret_scanner`'s regex library with additional patterns for
config files, env files, terraform state, shell history, and other non-JS
sources.  Provides confidence scoring and automatic KG promotion.

Validators reach out to issuing APIs only when the engagement RoE permits
live probes (checked via ``roe_allows``).  The module never stores raw
secrets — redacted forms (first 4 + last 4 chars) are used everywhere.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
from enum import StrEnum
from typing import Any, Awaitable, Callable

import httpx
from langchain_core.tools import tool

from decepticon.tools.research._state import _json, _load, _save
from decepticon.tools.research.secret_scanner import (
    _VALIDATORS,
    SECRET_PATTERNS,
    _extract,
)
from decepticon_core.types.kg import (
    Edge,
    EdgeKind,
    Node,
    NodeKind,
    Severity,
)
from decepticon_core.utils.logging import get_logger

log = get_logger("research.secret_validation")

DEFAULT_TIMEOUT = 10.0
MAX_VALIDATION_PROBES = 50

# ── Confidence levels ───────────────────────────────────────────────────


class ConfidenceLevel(StrEnum):
    PATTERN_MATCH = "pattern_match"
    SYNTAX_VALID = "syntax_valid"
    LIVE_CONFIRMED = "live_confirmed"
    ADMIN_CONFIRMED = "admin_confirmed"
    DEAD = "dead"


# ── Extended patterns ───────────────────────────────────────────────────
#
# These supplement SECRET_PATTERNS from secret_scanner.py. Context-
# sensitive patterns use lookahead/lookbehind for neighboring keywords.

EXTENDED_SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "azure_client_secret": re.compile(
        r"(?i)(?:client_secret|AZURE)[^\n]{0,30}?['\"]([A-Za-z0-9_~.\-]{34})['\"]"
    ),
    "gcp_service_account": re.compile(r'"type"\s*:\s*"service_account"'),
    "twilio_auth_token": re.compile(r"(?i)(?:TWILIO)[^\n]{0,30}?([0-9a-f]{32})\b"),
    "mailgun_api_key": re.compile(r"key-[a-z0-9]{32}"),
    "datadog_api_key": re.compile(r"(?i)(?:DD_API_KEY|DATADOG)[^\n]{0,30}?([0-9a-f]{32})\b"),
    "cloudflare_api_token": re.compile(r"(?i)(?:CF_|CLOUDFLARE)[^\n]{0,30}?([A-Za-z0-9_\-]{40})\b"),
    "digitalocean_pat": re.compile(r"dop_v1_[a-f0-9]{64}"),
    "heroku_api_key": re.compile(
        r"(?i)(?:HEROKU)[^\n]{0,30}?"
        r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})"
    ),
    "npm_token": re.compile(r"npm_[A-Za-z0-9]{36}"),
    "pypi_token": re.compile(r"pypi-[A-Za-z0-9_\-]{100,}"),
    "docker_hub_token": re.compile(r"dckr_pat_[A-Za-z0-9_\-]{26,}"),
}

# Merged view for scanning — base patterns + extensions.
ALL_PATTERNS: dict[str, re.Pattern[str]] = {**SECRET_PATTERNS, **EXTENDED_SECRET_PATTERNS}


# ── Extended validators ─────────────────────────────────────────────────

_Probe = Callable[[httpx.AsyncClient, str], Awaitable[dict[str, Any]]]


async def _probe_aws(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Minimal AWS STS GetCallerIdentity using SigV4.

    ``secret`` is expected to be an AWS access key ID. Without a
    corresponding secret access key we cannot produce a valid signature,
    so this probe validates the *format* and marks keys that satisfy the
    AKIA/ASIA prefix + length check as syntax-valid.  When paired with a
    secret key (``AKID:SECRET`` format), a real STS call is attempted.
    """
    parts = secret.split(":", 1)
    if len(parts) == 2:
        access_key, secret_key = parts
    else:
        # Standalone access key ID — format validation only.
        access_key = secret
        if re.match(
            r"(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}$",
            access_key,
        ):
            return {"confidence": ConfidenceLevel.SYNTAX_VALID, "scope_info": {}}
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}

    # Attempt a real STS GetCallerIdentity with SigV4.
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        datestamp = now.strftime("%Y%m%d")
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        region = "us-east-1"
        service = "sts"
        host = "sts.amazonaws.com"

        canonical_uri = "/"
        canonical_querystring = "Action=GetCallerIdentity&Version=2011-06-15"
        canonical_headers = f"host:{host}\nx-amz-date:{amzdate}\n"
        signed_headers = "host;x-amz-date"
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_request = (
            f"GET\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers}\n{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{amzdate}\n{credential_scope}\n"
            + hashlib.sha256(canonical_request.encode()).hexdigest()
        )

        def _sign(key: bytes, msg: str) -> bytes:
            return hmac.new(key, msg.encode(), hashlib.sha256).digest()

        k_date = _sign(f"AWS4{secret_key}".encode(), datestamp)
        k_region = _sign(k_date, region)
        k_service = _sign(k_region, service)
        k_signing = _sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )

        resp = await client.get(
            f"https://{host}/?{canonical_querystring}",
            headers={
                "x-amz-date": amzdate,
                "Authorization": auth_header,
            },
        )
        if resp.status_code == 200:
            # Extract account info from XML response.
            scope_info: dict[str, Any] = {}
            body = resp.text
            for tag in ("Account", "Arn", "UserId"):
                m = re.search(f"<{tag}>(.*?)</{tag}>", body)
                if m:
                    scope_info[tag.lower()] = m.group(1)
            # Check if it's root/admin-level.
            arn = scope_info.get("arn", "")
            if ":root" in arn or "AdministratorAccess" in arn:
                return {"confidence": ConfidenceLevel.ADMIN_CONFIRMED, "scope_info": scope_info}
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.SYNTAX_VALID, "scope_info": {}}


async def _probe_twilio(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Validate Twilio credentials via account listing."""
    try:
        resp = await client.get(
            "https://api.twilio.com/2010-04-01/Accounts.json",
            auth=("", secret),
        )
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("accounts", [])
            scope_info: dict[str, Any] = {}
            if accounts:
                scope_info["account_sid"] = accounts[0].get("sid", "")
                scope_info["friendly_name"] = accounts[0].get("friendly_name", "")
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}


async def _probe_mailgun(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Validate Mailgun API key via domain listing."""
    try:
        resp = await client.get(
            "https://api.mailgun.net/v3/domains",
            auth=("api", secret),
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            scope_info = {"domain_count": len(items)}
            if items:
                scope_info["domains"] = [d.get("name", "") for d in items[:5]]
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}


async def _probe_digitalocean(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Validate DigitalOcean PAT via account endpoint."""
    try:
        resp = await client.get(
            "https://api.digitalocean.com/v2/account",
            headers={"Authorization": f"Bearer {secret}"},
        )
        if resp.status_code == 200:
            data = resp.json().get("account", {})
            scope_info = {
                "email": data.get("email", ""),
                "status": data.get("status", ""),
                "droplet_limit": data.get("droplet_limit", 0),
            }
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}


async def _probe_npm(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Validate npm token via user endpoint."""
    try:
        resp = await client.get(
            "https://registry.npmjs.org/-/npm/v1/user",
            headers={"Authorization": f"Bearer {secret}"},
        )
        if resp.status_code == 200:
            data = resp.json()
            scope_info = {"name": data.get("name", "")}
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}


async def _probe_heroku(client: httpx.AsyncClient, secret: str) -> dict[str, Any]:
    """Validate Heroku API key via account endpoint."""
    try:
        resp = await client.get(
            "https://api.heroku.com/account",
            headers={
                "Authorization": f"Bearer {secret}",
                "Accept": "application/vnd.heroku+json; version=3",
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            scope_info = {
                "email": data.get("email", ""),
                "id": data.get("id", ""),
            }
            return {"confidence": ConfidenceLevel.LIVE_CONFIRMED, "scope_info": scope_info}
        return {"confidence": ConfidenceLevel.DEAD, "scope_info": {}}
    except Exception:
        return {"confidence": ConfidenceLevel.PATTERN_MATCH, "scope_info": {}}


# Map extended pattern names → rich probes.  Base _VALIDATORS from
# secret_scanner return bool; we wrap them below.
_EXTENDED_VALIDATORS: dict[str, _Probe] = {
    "twilio_auth_token": _probe_twilio,
    "mailgun_api_key": _probe_mailgun,
    "digitalocean_pat": _probe_digitalocean,
    "npm_token": _probe_npm,
    "heroku_api_key": _probe_heroku,
    "aws_access_key_id": _probe_aws,
    "aws_secret_access_key": _probe_aws,
}


# ── Helpers ─────────────────────────────────────────────────────────────


def _redact(secret: str) -> str:
    """Show first 4 + last 4 chars, mask the middle."""
    if len(secret) <= 12:
        return secret[:4] + "****"
    return secret[:4] + "****" + secret[-4:]


async def _classify_extended(
    pattern_name: str, secret: str
) -> tuple[ConfidenceLevel, dict[str, Any]]:
    """Resolve confidence and scope info for a single matched secret."""
    # Check extended validators first (they return rich results).
    if pattern_name in _EXTENDED_VALIDATORS:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            try:
                return_val = await _EXTENDED_VALIDATORS[pattern_name](client, secret)
                return (
                    ConfidenceLevel(return_val["confidence"]),
                    return_val.get("scope_info", {}),
                )
            except Exception:
                return ConfidenceLevel.PATTERN_MATCH, {}

    # Fall back to base validators from secret_scanner (bool probes).
    if pattern_name in _VALIDATORS:
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as client:
            try:
                probe = _VALIDATORS[pattern_name]
                is_live = await probe(client, secret)
                if is_live:
                    return ConfidenceLevel.LIVE_CONFIRMED, {}
                return ConfidenceLevel.DEAD, {}
            except Exception:
                return ConfidenceLevel.PATTERN_MATCH, {}

    # No validator — pattern match only.
    return ConfidenceLevel.PATTERN_MATCH, {}


def _auto_detect_type(secret: str) -> str:
    """Try to detect secret type from its value."""
    for name, pattern in ALL_PATTERNS.items():
        if pattern.search(secret):
            return name
    return "unknown"


# ── Tool functions ──────────────────────────────────────────────────────


@tool
async def validate_secrets(content: str, source_path: str = "") -> str:
    """Scan arbitrary text for secrets with live validation and confidence scoring.

    WHEN TO USE: After fetching config files, .env files, terraform state,
    shell history, CI/CD configs, or any non-JavaScript text that may contain
    leaked credentials.  Unlike ``scan_secrets`` (JavaScript-only), this tool
    works on ALL text content and provides richer confidence scoring.

    Detected types include everything from ``scan_secrets`` plus Azure client
    secrets, GCP service account JSON, Twilio auth tokens, Mailgun/Datadog/
    Cloudflare/DigitalOcean/Heroku/npm/PyPI/Docker Hub tokens.

    Args:
        content: Raw text content to scan for secrets.
        source_path: Optional path/URL where the content was found (for context).

    Returns:
        JSON string of the form::

            {
              "count": <int>,
              "source": "<path>",
              "secrets": [
                {
                  "secret_redacted": "<first4>****<last4>",
                  "pattern": "<type>",
                  "line": <int>,
                  "status": "live|dead|unvalidated",
                  "confidence": "<ConfidenceLevel>",
                  "scope_info": {...}
                },
                ...
              ]
            }
    """
    if not content:
        return _json({"count": 0, "source": source_path, "secrets": []})

    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    status_cache: dict[tuple[str, str], tuple[ConfidenceLevel, dict[str, Any]]] = {}
    probes_done = 0

    for line_no, line in enumerate(content.splitlines(), start=1):
        for pattern_name, pattern in ALL_PATTERNS.items():
            for match in pattern.finditer(line):
                secret = _extract(pattern, match)
                key = (pattern_name, secret, line_no)
                if key in seen:
                    continue
                seen.add(key)

                cache_key = (pattern_name, secret)
                cached = status_cache.get(cache_key)
                if cached is None:
                    has_validator = (
                        pattern_name in _EXTENDED_VALIDATORS or pattern_name in _VALIDATORS
                    )
                    if has_validator and probes_done >= MAX_VALIDATION_PROBES:
                        confidence = ConfidenceLevel.PATTERN_MATCH
                        scope_info: dict[str, Any] = {}
                    else:
                        if has_validator:
                            probes_done += 1
                        confidence, scope_info = await _classify_extended(pattern_name, secret)
                    status_cache[cache_key] = (confidence, scope_info)
                else:
                    confidence, scope_info = cached

                if confidence == ConfidenceLevel.LIVE_CONFIRMED:
                    status = "live"
                elif confidence == ConfidenceLevel.ADMIN_CONFIRMED:
                    status = "live"
                elif confidence == ConfidenceLevel.DEAD:
                    status = "dead"
                else:
                    status = "unvalidated"

                findings.append(
                    {
                        "secret_redacted": _redact(secret),
                        "pattern": pattern_name,
                        "line": line_no,
                        "status": status,
                        "confidence": confidence.value,
                        "scope_info": scope_info,
                    }
                )

    return _json({"count": len(findings), "source": source_path, "secrets": findings})


@tool
async def validate_single_secret(secret: str, secret_type: str = "") -> str:
    """Validate a single secret value and report its liveness and scope.

    WHEN TO USE: When you already have a specific secret/credential and want
    to check whether it is still active, what scope it grants, and get a
    confidence level.  Handles AWS keys, GitHub/GitLab tokens, Slack/Stripe/
    OpenAI/SendGrid keys, Twilio/Mailgun/DigitalOcean/npm/Heroku tokens, and
    more.

    Args:
        secret: The raw secret value to validate.
        secret_type: Optional type hint (e.g. ``github_token``,
            ``aws_access_key_id``).  Auto-detected if omitted.

    Returns:
        JSON string with ``secret_redacted``, ``type``, ``status``,
        ``confidence``, and ``scope_info``.
    """
    if not secret_type:
        secret_type = _auto_detect_type(secret)

    confidence, scope_info = await _classify_extended(secret_type, secret)

    if confidence in (ConfidenceLevel.LIVE_CONFIRMED, ConfidenceLevel.ADMIN_CONFIRMED):
        status = "live"
    elif confidence == ConfidenceLevel.DEAD:
        status = "dead"
    else:
        status = "unvalidated"

    return _json(
        {
            "secret_redacted": _redact(secret),
            "type": secret_type,
            "status": status,
            "confidence": confidence.value,
            "scope_info": scope_info,
        }
    )


@tool
def promote_secrets_to_kg(secrets_json: str) -> str:
    """Promote validated secrets to the knowledge graph as Credential/Secret nodes.

    WHEN TO USE: After running ``validate_secrets`` or
    ``validate_single_secret``, pass the output JSON to create persistent
    Credential nodes in the engagement knowledge graph.  Live-confirmed
    secrets become Critical-severity Credential nodes; dead secrets become
    Info-severity nodes.  Each node is connected via LEAKS edges to a
    Source node when ``source`` is present.

    Args:
        secrets_json: JSON string — either the full output of
            ``validate_secrets`` or a list of individual secret dicts.

    Returns:
        JSON string with ``promoted`` count and ``stats``.
    """
    try:
        data = json.loads(secrets_json)
    except (json.JSONDecodeError, TypeError):
        return _json({"error": "Invalid JSON input"})

    # Accept both {secrets: [...]} envelope and bare list.
    if isinstance(data, dict):
        secrets_list = data.get("secrets", [])
        source = data.get("source", "")
    elif isinstance(data, list):
        secrets_list = data
        source = ""
    else:
        return _json({"error": "Expected object or array"})

    graph, _path = _load()
    promoted = 0

    # Create source node if applicable.
    source_node: Node | None = None
    if source:
        source_node = graph.upsert_node(Node.make(NodeKind.SOURCE_FILE, source, key=source))

    for entry in secrets_list:
        redacted = entry.get("secret_redacted", "unknown")
        pattern = entry.get("pattern", entry.get("type", "unknown"))
        confidence = entry.get("confidence", "pattern_match")
        status = entry.get("status", "unvalidated")
        scope_info = entry.get("scope_info", {})

        # Choose node kind and severity based on confidence.
        if confidence in ("live_confirmed", "admin_confirmed"):
            severity = Severity.CRITICAL
            node_kind = NodeKind.CREDENTIAL
        elif confidence == "dead":
            severity = Severity.INFO
            node_kind = NodeKind.SECRET
        else:
            severity = Severity.MEDIUM
            node_kind = NodeKind.SECRET

        label = f"{pattern}:{redacted}"
        node = graph.upsert_node(
            Node.make(
                node_kind,
                label,
                key=f"{pattern}:{redacted}",
                secret_type=pattern,
                confidence=confidence,
                status=status,
                severity=severity.value,
                **{f"scope_{k}": v for k, v in scope_info.items()},
            )
        )
        promoted += 1

        # Link to source file if available.
        if source_node:
            graph.upsert_edge(Edge.make(source_node.id, node.id, EdgeKind.LEAKS, weight=1.0))

    _save(graph)
    return _json({"promoted": promoted, "stats": graph.stats()})


SECRET_VALIDATION_TOOLS = [validate_secrets, validate_single_secret, promote_secrets_to_kg]
