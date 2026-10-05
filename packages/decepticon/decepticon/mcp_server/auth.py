"""Bearer-token authentication for the MCP bridge's network transport.

The bridge can launch authorized engagements, so binding the streamable-http
transport to a non-loopback interface without auth would let anyone who reaches
the port drive Decepticon. This builds the MCP SDK's :class:`TokenVerifier` for
two modes, both plugged into ``FastMCP(token_verifier=..., auth=AuthSettings)``
so the SDK installs its ``BearerAuthBackend`` + ``RequireAuthMiddleware`` (401
on a missing or invalid ``Authorization: Bearer`` token):

- ``shared-secret`` — one token compared in constant time. No external issuer;
  the operator sets ``DECEPTICON_MCP_TOKEN``. OSS / local default.
- ``jwt`` — validates an RS256/ES256 bearer JWT against a JWKS endpoint or a
  static public key, checking ``iss`` and ``aud``. This is the OAuth 2.1
  resource-server posture the MCP June-2025 spec prescribes for remote servers.

Decepticon depends on the official ``mcp`` SDK (``mcp.server.fastmcp``), not the
standalone ``fastmcp`` package, so auth is wired through the SDK's
``TokenVerifier`` protocol rather than ``fastmcp``'s ``JWTVerifier``.
"""

from __future__ import annotations

import hmac
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

if TYPE_CHECKING:
    from decepticon.mcp_server.config import ServerConfig

#: Asymmetric signatures the JWT verifier accepts. Symmetric (HS*) is excluded
#: on purpose — a resource server validating an HMAC JWT would need the signing
#: secret, collapsing the trust boundary back to shared-secret mode.
_JWT_ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512")

#: Hosts that bind only the local loopback interface (no network exposure).
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"})


def is_loopback_host(host: str) -> bool:
    """True when ``host`` binds only the local loopback interface."""
    return host.strip().lower() in _LOOPBACK_HOSTS


class SharedSecretVerifier(TokenVerifier):
    """Accepts exactly one bearer token, compared in constant time."""

    def __init__(self, secret: str, *, resource: str | None) -> None:
        self._secret = secret
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or not hmac.compare_digest(token, self._secret):
            return None
        return AccessToken(
            token=token,
            client_id="decepticon-mcp:shared-secret",
            scopes=[],
            resource=self._resource,
        )


class JwtVerifier(TokenVerifier):
    """Validates a bearer JWT against a JWKS endpoint or a static public key.

    ``iss`` and ``aud`` are both enforced: audience binding is what stops a
    token minted for another resource server being replayed against this one.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_uri: str | None,
        public_key: str | None,
    ) -> None:
        import jwt  # provided by the [mcp] extra (pyjwt[crypto]); imported here

        # so the shared-secret path never pays the crypto-import cost.
        self._jwt = jwt
        self._issuer = issuer
        self._audience = audience
        self._jwk_client = jwt.PyJWKClient(jwks_uri) if jwks_uri else None
        self._public_key = public_key

    async def verify_token(self, token: str) -> AccessToken | None:
        jwt = self._jwt
        try:
            if self._jwk_client is not None:
                key = self._jwk_client.get_signing_key_from_jwt(token).key
            elif self._public_key is not None:
                key = self._public_key
            else:  # pragma: no cover - build_auth guarantees one key source
                return None
            claims = jwt.decode(
                token,
                key,
                algorithms=list(_JWT_ALGORITHMS),
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError:
            return None
        return AccessToken(
            token=token,
            client_id=str(
                claims.get("client_id") or claims.get("azp") or claims.get("sub") or "jwt"
            ),
            scopes=_claim_scopes(claims),
            expires_at=claims.get("exp"),
            resource=self._audience,
        )


def _claim_scopes(claims: dict[str, object]) -> list[str]:
    """Read OAuth scopes from the space-delimited ``scope`` or list ``scopes`` claim."""
    granted = claims.get("scope")
    if isinstance(granted, str):
        return granted.split()
    raw = claims.get("scopes")
    if isinstance(raw, (list, tuple)):
        return [str(s) for s in raw]
    return []


def resolve_auth_mode(config: ServerConfig) -> str:
    """Resolve the effective auth mode: explicit override, else inferred from config."""
    if config.auth_mode:
        return config.auth_mode
    if config.auth_token:
        return "shared-secret"
    if config.issuer and (config.jwks_uri or config.public_key):
        return "jwt"
    return "none"


def open_bind_error(mode: str, transport: str, host: str) -> str | None:
    """Error string if this bind would expose an unauthenticated network port.

    Only the ``streamable-http`` transport opens a socket; ``stdio`` is a local
    pipe, so auth is moot there.
    """
    if transport != "streamable-http" or is_loopback_host(host) or mode != "none":
        return None
    return (
        f"Refusing to start: --host {host} binds a non-loopback interface with no "
        "authentication. Set DECEPTICON_MCP_TOKEN (shared-secret), or "
        "--issuer + --jwks-uri/--auth-public-key + --audience (JWT), or bind "
        "--host 127.0.0.1 for local-only access."
    )


def build_auth(
    config: ServerConfig, *, host: str, port: int
) -> tuple[TokenVerifier | None, AuthSettings | None]:
    """Build the SDK token verifier + auth settings for the resolved mode.

    Returns ``(None, None)`` for ``mode == "none"`` so the server stays
    auth-free for stdio / loopback use. Raises :class:`ValueError` on a
    selected-but-incomplete auth configuration.
    """
    mode = resolve_auth_mode(config)
    if mode == "none":
        return None, None

    resource = config.resource_url or f"http://{host}:{port}/"
    if mode == "shared-secret":
        if not config.auth_token:
            raise ValueError("shared-secret auth selected but DECEPTICON_MCP_TOKEN is empty")
        verifier: TokenVerifier = SharedSecretVerifier(config.auth_token, resource=resource)
        return verifier, AuthSettings(
            issuer_url=AnyHttpUrl(resource),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=[],
        )
    if mode == "jwt":
        if not config.issuer:
            raise ValueError("jwt auth selected but issuer is unset")
        if not config.audience:
            raise ValueError("jwt auth selected but audience is unset")
        if not config.jwks_uri and not config.public_key:
            raise ValueError("jwt auth selected but neither jwks_uri nor public_key is set")
        verifier = JwtVerifier(
            issuer=config.issuer,
            audience=config.audience,
            jwks_uri=config.jwks_uri,
            public_key=_load_pem(config.public_key),
        )
        return verifier, AuthSettings(
            issuer_url=AnyHttpUrl(config.issuer),
            resource_server_url=AnyHttpUrl(resource),
            required_scopes=list(config.required_scopes),
        )
    raise ValueError(f"unknown auth mode: {mode!r}")


def _load_pem(value: str | None) -> str | None:
    """Resolve a public key given as a filesystem path or inline PEM text."""
    if not value:
        return None
    if value.startswith("-----BEGIN ") and "\n" in value:
        return value
    candidate = Path(value)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    return value
