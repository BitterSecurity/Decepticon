"""Tests for decepticon.mcp_server.auth — the bearer gate on the HTTP transport."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

# The bridge's bearer auth is built on the MCP SDK, which only ships with the
# optional [mcp] extra. Skip the whole module when it's absent (default CI lane)
# so collection doesn't error; the mcp-installed lane exercises it.
pytest.importorskip("mcp")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from decepticon.mcp_server.auth import (  # noqa: E402
    _load_pem,
    build_auth,
    is_loopback_host,
    open_bind_error,
    resolve_auth_mode,
)
from decepticon.mcp_server.config import ServerConfig  # noqa: E402


def _cfg(**kw: object) -> ServerConfig:
    base: dict[str, object] = {
        "langgraph_url": "http://localhost:2024",
        "default_assistant": "decepticon",
        "request_timeout_seconds": 60.0,
    }
    base.update(kw)
    return ServerConfig(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("::", False),
        ("10.0.0.5", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    assert is_loopback_host(host) is expected


def test_resolve_mode_none_by_default() -> None:
    assert resolve_auth_mode(_cfg()) == "none"


def test_resolve_mode_infers_shared_secret_from_token() -> None:
    assert resolve_auth_mode(_cfg(auth_token="s")) == "shared-secret"


def test_resolve_mode_infers_jwt_from_issuer_and_key() -> None:
    assert resolve_auth_mode(_cfg(issuer="https://i/", jwks_uri="https://i/jwks")) == "jwt"


def test_resolve_mode_explicit_override_wins_over_inference() -> None:
    assert resolve_auth_mode(_cfg(auth_mode="none", auth_token="s")) == "none"


def test_open_bind_blocks_public_http_without_auth() -> None:
    assert open_bind_error("none", "streamable-http", "0.0.0.0")


def test_open_bind_allows_loopback_without_auth() -> None:
    assert open_bind_error("none", "streamable-http", "127.0.0.1") is None


def test_open_bind_allows_public_http_with_auth() -> None:
    assert open_bind_error("shared-secret", "streamable-http", "0.0.0.0") is None


def test_open_bind_ignores_stdio() -> None:
    assert open_bind_error("none", "stdio", "0.0.0.0") is None


def test_build_auth_none_returns_nothing() -> None:
    assert build_auth(_cfg(), host="127.0.0.1", port=8765) == (None, None)


def test_shared_secret_accepts_exact_token() -> None:
    verifier, auth = build_auth(_cfg(auth_token="hunter2"), host="0.0.0.0", port=8765)
    assert verifier is not None and auth is not None
    access = asyncio.run(verifier.verify_token("hunter2"))
    assert access is not None
    assert access.client_id == "decepticon-mcp:shared-secret"


def test_shared_secret_rejects_wrong_and_empty_token() -> None:
    verifier, _ = build_auth(_cfg(auth_token="hunter2"), host="0.0.0.0", port=8765)
    assert verifier is not None
    assert asyncio.run(verifier.verify_token("nope")) is None
    assert asyncio.run(verifier.verify_token("")) is None


def test_shared_secret_empty_secret_is_error() -> None:
    with pytest.raises(ValueError, match="DECEPTICON_MCP_TOKEN"):
        build_auth(_cfg(auth_mode="shared-secret"), host="0.0.0.0", port=8765)


@pytest.fixture(scope="module")
def rsa_keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return priv, pub


def _jwt_cfg(pub: str, **kw: object) -> ServerConfig:
    return _cfg(issuer="https://issuer/", audience="decepticon-mcp", public_key=pub, **kw)


def test_inline_pem_is_not_treated_as_a_path() -> None:
    inline = "-----BEGIN PUBLIC KEY-----\n" + "A" * 300 + "\n-----END PUBLIC KEY-----\n"
    assert _load_pem(inline) == inline


def test_public_key_path_is_read(tmp_path: Path) -> None:
    key_file = tmp_path / "public.pem"
    key_file.write_text("public key text", encoding="utf-8")
    assert _load_pem(str(key_file)) == "public key text"


def test_jwt_accepts_valid_token_and_reads_scopes(rsa_keys: tuple[str, str]) -> None:
    priv, pub = rsa_keys
    verifier, auth = build_auth(
        _jwt_cfg(pub, required_scopes=("engage",)), host="0.0.0.0", port=8765
    )
    assert verifier is not None and auth is not None
    assert list(auth.required_scopes or []) == ["engage"]
    token = jwt.encode(
        {"iss": "https://issuer/", "aud": "decepticon-mcp", "sub": "agent", "scope": "engage read"},
        priv,
        algorithm="RS256",
    )
    access = asyncio.run(verifier.verify_token(token))
    assert access is not None
    assert access.client_id == "agent"
    assert "engage" in access.scopes


def test_jwt_rejects_wrong_audience(rsa_keys: tuple[str, str]) -> None:
    priv, pub = rsa_keys
    verifier, _ = build_auth(_jwt_cfg(pub), host="0.0.0.0", port=8765)
    assert verifier is not None
    token = jwt.encode(
        {"iss": "https://issuer/", "aud": "someone-else", "sub": "a"}, priv, algorithm="RS256"
    )
    assert asyncio.run(verifier.verify_token(token)) is None


def test_jwt_rejects_wrong_issuer(rsa_keys: tuple[str, str]) -> None:
    priv, pub = rsa_keys
    verifier, _ = build_auth(_jwt_cfg(pub), host="0.0.0.0", port=8765)
    assert verifier is not None
    token = jwt.encode(
        {"iss": "https://evil/", "aud": "decepticon-mcp", "sub": "a"}, priv, algorithm="RS256"
    )
    assert asyncio.run(verifier.verify_token(token)) is None


def test_jwt_rejects_garbage_token(rsa_keys: tuple[str, str]) -> None:
    _, pub = rsa_keys
    verifier, _ = build_auth(_jwt_cfg(pub), host="0.0.0.0", port=8765)
    assert verifier is not None
    assert asyncio.run(verifier.verify_token("not.a.jwt")) is None


def test_jwt_requires_issuer() -> None:
    with pytest.raises(ValueError, match="issuer"):
        build_auth(
            _cfg(auth_mode="jwt", audience="a", jwks_uri="https://j/"), host="0.0.0.0", port=8765
        )


def test_jwt_requires_audience() -> None:
    with pytest.raises(ValueError, match="audience"):
        build_auth(
            _cfg(auth_mode="jwt", issuer="https://i/", jwks_uri="https://j/"),
            host="0.0.0.0",
            port=8765,
        )


def test_jwt_requires_a_key_source() -> None:
    with pytest.raises(ValueError, match="jwks_uri nor public_key"):
        build_auth(
            _cfg(auth_mode="jwt", issuer="https://i/", audience="a"), host="0.0.0.0", port=8765
        )


def _middleware_names(app: object) -> set[str]:
    return {
        getattr(m, "cls", type(m)).__name__
        for m in app.user_middleware  # type: ignore[attr-defined]
    }


def test_build_server_installs_auth_middleware_when_secret_set() -> None:
    from decepticon.mcp_server.server import build_server

    server = build_server(_cfg(auth_token="x"), client=object(), host="0.0.0.0", port=8765)
    assert "AuthenticationMiddleware" in _middleware_names(server.streamable_http_app())


def test_build_server_skips_auth_middleware_when_unset() -> None:
    from decepticon.mcp_server.server import build_server

    server = build_server(_cfg(), client=object(), host="127.0.0.1", port=8765)
    assert "AuthenticationMiddleware" not in _middleware_names(server.streamable_http_app())
