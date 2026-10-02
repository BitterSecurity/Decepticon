"""LangChain @tool wrappers for the web exploitation suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from decepticon.tools.bash.bash import _current_workspace_path
from decepticon.tools.web.graphql import GraphQLSchema
from decepticon.tools.web.http import HTTPSession
from decepticon.tools.web.jwt import (
    DEFAULT_WEAK_SECRETS,
    crack_hs_secret,
    forge_token,
    parse_token,
)
from decepticon.tools.web.oauth import analyze_oauth_callback
from decepticon.tools.web.session import analyze_cookie


def _json(data: Any) -> str:
    return json.dumps(data, indent=2, default=str, ensure_ascii=False)


@tool
def jwt_parse(token: str) -> str:
    """Parse a JWT and surface known header / claim findings.

    Returns a JSON object with the decoded header, claims, and any
    security findings (alg=none, no-exp, jku/kid injection, etc.).
    """
    t = parse_token(token)
    return _json(
        {
            "header": t.header.to_dict(),
            "claims": t.claims.to_dict(),
            "findings": list(t.findings),
            "expired": t.claims.expired,
        }
    )


@tool
def jwt_forge(
    claims_json: str,
    alg: str = "none",
    secret: str = "",
    header_json: str = "",
) -> str:
    """Forge a JWT with arbitrary claims/algorithm.

    Args:
        claims_json: JSON object for the body. Example:
            ``'{"sub":"admin","exp":9999999999}'``
        alg: none | HS256 | HS384 | HS512
        secret: Required for HS* algs
        header_json: Optional JSON for extra header fields (kid, jku, x5u)

    Returns:
        JSON with the forged token string.
    """
    try:
        claims = json.loads(claims_json) if claims_json else {}
        header = json.loads(header_json) if header_json else None
        token = forge_token(claims, alg=alg, secret=secret or None, header=header)
    except (json.JSONDecodeError, ValueError) as e:
        return _json({"error": str(e)})
    return _json({"token": token})


@tool
def jwt_crack(token: str, wordlist: str = "") -> str:
    """Dictionary-attack an HS* JWT with a candidate wordlist.

    ``wordlist`` is a newline-separated list. If empty, the default
    weak-secret list is used (seeds from ``DEFAULT_WEAK_SECRETS``).
    """
    t = parse_token(token)
    candidates = wordlist.splitlines() if wordlist else list(DEFAULT_WEAK_SECRETS)
    secret = crack_hs_secret(t, candidates)
    return _json({"cracked": secret is not None, "secret": secret, "tried": len(candidates)})


@tool
def graphql_plan(introspection_json: str) -> str:
    """Parse a GraphQL introspection blob, list IDOR candidates, and
    auto-generate a baseline query for each.

    ``introspection_json`` should be the full JSON body returned by the
    server for the introspection query.
    """
    try:
        data = json.loads(introspection_json)
    except json.JSONDecodeError as e:
        return _json({"error": f"introspection must be JSON: {e}"})
    schema = GraphQLSchema.from_introspection(data)
    candidates = [
        {
            "kind": kind,
            "field": fld.name,
            "args": list(fld.args),
            "sample_query": schema.generate_query(fld.name, kind=kind.lower()),
        }
        for kind, fld in schema.idor_candidates()
    ]
    return _json(
        {
            "query_type": schema.query_type,
            "mutation_type": schema.mutation_type,
            "idor_candidates": candidates,
            "query_count": len(schema.query_fields()),
            "mutation_count": len(schema.mutation_fields()),
        }
    )


@tool
def oauth_audit(
    callback_url: str,
    initial_request_url: str = "",
    public_client: bool = False,
) -> str:
    """Audit an OAuth / OIDC callback URL for canonical RFC issues.

    Flags missing/predictable state, missing nonce, implicit flow,
    PKCE absence, open redirect_uri, scope over-request, etc.
    """
    findings = analyze_oauth_callback(
        callback_url,
        initial_request_url=initial_request_url or None,
        public_client=public_client,
    )
    return _json([f.to_dict() for f in findings])


@tool
def cookie_audit(
    name: str,
    value: str,
    secure: bool = False,
    http_only: bool = False,
    same_site: str = "",
) -> str:
    """Classify a cookie and flag framework + entropy + transport issues."""
    analysis = analyze_cookie(
        name,
        value,
        secure=secure,
        http_only=http_only,
        same_site=same_site or None,
    )
    return _json(analysis.to_dict())


_session: HTTPSession | None = None

_COOKIE_JAR_RELPATH = "exploit/creds/http_session.json"


def _host_of(value: str) -> str:
    """Best-effort host extraction from a target URL or bare host string."""
    import httpx

    candidate = value if "://" in value else f"http://{value}"
    try:
        return (httpx.URL(candidate).host or "").lower()
    except (ValueError, TypeError):
        return ""


def _cookie_jar_path(workspace: str) -> Path:
    return Path(workspace.rstrip("/") or workspace) / _COOKIE_JAR_RELPATH


def _seed_session_from_workspace(session: HTTPSession, workspace: str) -> None:
    """Seed the session's auth headers/cookies from ``plan/credentials.json``.

    Host-scoped: each credential's headers and cookies are bound to the host
    named by its ``target`` (falling back to ``login_url``). A credential with
    neither cannot be host-scoped and is skipped here — the agent still sees it
    in the injected context block and can authenticate manually.
    """
    cred_path = Path(workspace.rstrip("/") or workspace) / "plan" / "credentials.json"
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    for entry in data.get("credentials", []):
        if not isinstance(entry, dict):
            continue
        host = _host_of(str(entry.get("target") or entry.get("login_url") or ""))
        if not host:
            continue
        headers = entry.get("headers") if isinstance(entry.get("headers"), dict) else None
        cookies = entry.get("cookies") if isinstance(entry.get("cookies"), dict) else None
        if headers or cookies:
            session.seed_credential(host=host, headers=headers, cookies=cookies)


def _get_session() -> HTTPSession:
    global _session
    if _session is None:
        verify_env = os.environ.get("DECEPTICON_HTTP_VERIFY_TLS", "").strip().lower()
        verify_tls = verify_env in {"1", "true", "yes", "on"}
        _session = HTTPSession(verify=verify_tls)
        workspace = _current_workspace_path.get()
        if workspace and workspace != "/workspace":
            _seed_session_from_workspace(_session, workspace)
            _session.load_cookies(_cookie_jar_path(workspace))
    return _session


@tool
async def http_request(
    method: str,
    url: str,
    headers_json: str = "{}",
    body: str = "",
    tag: str = "",
) -> str:
    """Send an HTTP request and return the response.

    The session is shared across calls: cookies set by a login response carry
    into later requests automatically. When the engagement provides credentials
    (`plan/credentials.json`), any operator-supplied auth headers/cookies are
    pre-seeded for the matching host, so authenticated requests need no manual
    header. Per-call ``headers_json`` overrides a seeded header.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS)
        url: Target URL
        headers_json: JSON string of headers dict
        body: Request body (for POST/PUT/PATCH)
        tag: Optional tag for organizing requests in history

    Returns:
        JSON with status, headers, body (truncated), elapsed_ms, request_id
    """
    try:
        headers = json.loads(headers_json) if headers_json else {}
    except json.JSONDecodeError:
        return _json({"error": "Invalid headers JSON"})

    # Async tool: await the session directly. The previous implementation
    # drove the coroutine via ``asyncio.get_event_loop().run_until_complete``,
    # which raises ``RuntimeError: ... cannot be called from a running event
    # loop`` under LangGraph's async runtime. Mirrors the async bash tools.
    try:
        session = _get_session()
        resp = await session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            body=body.encode() if body else None,
            tag=tag,
        )
        # Persist the jar so a login performed in one agent's process is
        # rehydrated by the next agent via load_cookies in _get_session.
        workspace = _current_workspace_path.get()
        if workspace and workspace != "/workspace":
            jar_path = _cookie_jar_path(workspace)
            jar_path.parent.mkdir(parents=True, exist_ok=True)
            session.save_cookies(jar_path)
        return _json(
            {
                "status": resp.status,
                "headers": dict(resp.headers),
                "body": resp.text(),
                "elapsed_ms": resp.elapsed_ms,
                "request_id": resp.request_id,
            }
        )
    except Exception as e:
        return _json({"error": f"{type(e).__name__}: {e}"})


@tool
def http_history(query: str = "", last_n: int = 10) -> str:
    """Search or list recent HTTP request/response history.

    Args:
        query: Search term (matches URL, method, status, tag). Empty = list recent.
        last_n: Number of recent entries to return (default 10)

    Returns:
        JSON list of {id, method, url, status, tag, elapsed_ms}
    """
    session = _get_session()
    if query:
        matches = session.history.search(url_substr=query)
    else:
        matches = list(session.history._entries)[-last_n:]
    entries = []
    for pair in matches:
        req, resp = pair if isinstance(pair, tuple) else (pair, None)
        entry: dict[str, Any] = {"id": req.id, "method": req.method, "url": req.url, "tag": req.tag}
        if resp:
            entry.update({"status": resp.status, "elapsed_ms": resp.elapsed_ms})
        entries.append(entry)
    return _json(entries[-last_n:])


from decepticon.tools.web.search import web_fetch, web_search  # noqa: E402

WEB_TOOLS = [
    jwt_parse,
    jwt_forge,
    jwt_crack,
    graphql_plan,
    oauth_audit,
    cookie_audit,
    http_request,
    http_history,
    # Open-web acquisition (ADR-0010) — sandbox-side engine, RoE-gated.
    web_search,
    web_fetch,
]
