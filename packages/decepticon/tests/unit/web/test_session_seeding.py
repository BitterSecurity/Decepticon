"""Operator-credential seeding + cookie-jar persistence on HTTPSession.

Covers the greybox session-reuse path: a credential's auth headers are sent
only to the host it names (never cross-origin, which matters because the web
HTTP tool runs host-side, outside the sandbox egress edge and follows
redirects), cookies are domain-scoped, and the jar round-trips to disk so a
login in one agent's process is rehydrated by the next.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from decepticon.tools.web.http import HTTPSession


def _mock_client(handler: Any) -> Any:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    return patch("decepticon.tools.web.http.httpx.AsyncClient", side_effect=patched)


class TestSeededHeaders:
    async def test_header_sent_to_seeded_host(self) -> None:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.host] = request.headers.get("authorization")
            return httpx.Response(200, content=b"ok")

        with _mock_client(handler):
            session = HTTPSession()
            session.seed_credential(
                host="app.example.com", headers={"Authorization": "Bearer tok"}
            )
            try:
                await session.request("GET", "https://app.example.com/me")
            finally:
                await session.close()

        assert seen["app.example.com"] == "Bearer tok"

    async def test_header_not_leaked_to_other_host(self) -> None:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.host] = request.headers.get("authorization")
            return httpx.Response(200, content=b"ok")

        with _mock_client(handler):
            session = HTTPSession()
            session.seed_credential(
                host="app.example.com", headers={"Authorization": "Bearer tok"}
            )
            try:
                await session.request("GET", "https://evil.example.net/collect")
            finally:
                await session.close()

        assert seen["evil.example.net"] is None

    async def test_per_call_header_overrides_seeded(self) -> None:
        seen: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen[request.url.host] = request.headers.get("authorization")
            return httpx.Response(200, content=b"ok")

        with _mock_client(handler):
            session = HTTPSession()
            session.seed_credential(host="app.example.com", headers={"Authorization": "Bearer A"})
            try:
                await session.request(
                    "GET", "https://app.example.com/me", headers={"Authorization": "Bearer B"}
                )
            finally:
                await session.close()

        assert seen["app.example.com"] == "Bearer B"


class TestSeededCookies:
    async def test_cookie_scoped_to_host(self) -> None:
        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            session = HTTPSession()
            session.seed_credential(host="app.example.com", cookies={"sid": "s3cret"})
            try:
                names = {c.name: c.domain for c in session._client.cookies.jar}
            finally:
                await session.close()
        assert names.get("sid") == "app.example.com"


class TestCookieJarPersistence:
    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            session = HTTPSession()
            session.seed_credential(host="app.example.com", cookies={"sid": "abc"})
            jar_path = tmp_path / "http_session.json"
            session.save_cookies(jar_path)

        payload = json.loads(jar_path.read_text(encoding="utf-8"))
        assert any(c["name"] == "sid" and c["value"] == "abc" for c in payload)

        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            restored = HTTPSession()
            restored.load_cookies(jar_path)
            names = {c.name: c.value for c in restored._client.cookies.jar}
        assert names.get("sid") == "abc"

    def test_load_missing_file_is_noop(self, tmp_path: Path) -> None:
        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            session = HTTPSession()
            session.load_cookies(tmp_path / "does-not-exist.json")
            assert list(session._client.cookies.jar) == []

    def test_load_malformed_file_is_noop(self, tmp_path: Path) -> None:
        bad = tmp_path / "http_session.json"
        bad.write_text("{not json", encoding="utf-8")
        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            session = HTTPSession()
            session.load_cookies(bad)
            assert list(session._client.cookies.jar) == []


class TestSeedCredentialGuards:
    def test_empty_host_ignored(self) -> None:
        with _mock_client(lambda r: httpx.Response(200, content=b"ok")):
            session = HTTPSession()
            session.seed_credential(host="   ", headers={"Authorization": "Bearer x"})
            assert session._seeded_headers == {}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
