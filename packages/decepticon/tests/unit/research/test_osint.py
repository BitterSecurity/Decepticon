"""Unit tests for the OSINT enrichment tool.

Network egress is exercised through ``httpx.MockTransport`` (no real
sockets); the offline path uses the deterministic mock catalog.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from decepticon.tools.research import osint
from decepticon.tools.research.osint import (
    _is_in_scope,
    _load_mock_catalog,
    _merge_findings,
    _new_findings,
    _normalize_domain,
    _parse_censys,
    _parse_shodan,
    _parse_zoomeye,
    osint_enrich,
)

_CRED_ENV = (
    "SHODAN_API_KEY",
    "CENSYS_API_ID",
    "CENSYS_API_SECRET",
    "ZOOMEYE_API_KEY",
    "DECEPTICON_OSINT_SCOPE",
    "DECEPTICON_OSINT_CATALOG",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from credentials / scope leaking from the host."""
    for name in _CRED_ENV:
        monkeypatch.delenv(name, raising=False)


def _install_async_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """Wire ``httpx.AsyncClient`` to a ``MockTransport`` so the tool's
    internally constructed client never touches the network."""
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _fake(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(*args, transport=transport, timeout=5.0, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake)


# ── Domain normalization ─────────────────────────────────────────────────


class TestNormalizeDomain:
    def test_strips_scheme_path_and_port(self) -> None:
        assert _normalize_domain("https://api.example.com:443/path?q=1") == "api.example.com"

    def test_lowercases_and_trims(self) -> None:
        assert _normalize_domain("  Example.COM.  ") == "example.com"

    def test_empty(self) -> None:
        assert _normalize_domain("") == ""


# ── Scope rules ──────────────────────────────────────────────────────────


class TestScope:
    def test_empty_scope_allows_everything(self) -> None:
        assert _is_in_scope("example.com", []) is True

    def test_exact_match(self) -> None:
        assert _is_in_scope("example.com", ["example.com"]) is True

    def test_wildcard_matches_only_direct_subdomain(self) -> None:
        assert _is_in_scope("api.example.com", ["*.example.com"]) is True
        assert _is_in_scope("example.com", ["*.example.com"]) is False
        assert _is_in_scope("a.b.example.com", ["*.example.com"]) is False

    def test_out_of_scope(self) -> None:
        assert _is_in_scope("evil.test", ["*.example.com"]) is False


# ── Findings merge / dedupe ──────────────────────────────────────────────


class TestMerge:
    def test_dedupes_ports_and_dict_records(self) -> None:
        dst = _new_findings()
        _merge_findings(dst, {"open_ports": [{"port": 80}, {"port": 80}, {"port": 443}]})
        _merge_findings(dst, {"open_ports": [{"port": 443}]})
        assert dst["open_ports"] == [{"port": 80}, {"port": 443}]


# ── Parsers ──────────────────────────────────────────────────────────────


class TestParsers:
    def test_parse_shodan(self) -> None:
        host = {
            "matches": [
                {
                    "ip_str": "203.0.113.5",
                    "port": 443,
                    "transport": "tcp",
                    "data": "HTTP/1.1 200 OK",
                    "ssl": {
                        "cert": {
                            "subject": {"CN": "example.com"},
                            "issuer": {"CN": "Let's Encrypt"},
                            "expires": "20301231235959Z",
                            "fingerprint": {"sha256": "deadbeef"},
                        }
                    },
                }
            ]
        }
        dns = {
            "domain": "example.com",
            "data": [{"subdomain": "www", "type": "A", "value": "203.0.113.5"}],
        }
        out = _parse_shodan(host, dns)
        assert {"port": 443, "transport": "tcp"} in out["open_ports"]
        assert out["certificates"][0]["subject_cn"] == "example.com"
        assert out["certificates"][0]["fingerprint_sha256"] == "deadbeef"
        assert out["banners"][0]["banner"] == "HTTP/1.1 200 OK"
        assert out["dns_records"][0]["value"] == "203.0.113.5"

    def test_parse_censys(self) -> None:
        data = {
            "result": {
                "hits": [
                    {
                        "ip": "203.0.113.6",
                        "services": [
                            {
                                "port": 80,
                                "service_name": "HTTP",
                                "banner": "Server: nginx",
                                "transport_protocol": "TCP",
                            }
                        ],
                        "dns": {"names": ["example.com"]},
                    }
                ]
            }
        }
        out = _parse_censys(data)
        assert {"port": 80, "transport": "tcp"} in out["open_ports"]
        assert out["banners"][0]["service"] == "HTTP"
        assert out["dns_records"][0]["name"] == "example.com"

    def test_parse_zoomeye(self) -> None:
        data = {
            "matches": [
                {
                    "ip": "203.0.113.7",
                    "portinfo": {"port": 22, "service": "ssh", "banner": "SSH-2.0-OpenSSH"},
                }
            ]
        }
        out = _parse_zoomeye(data)
        assert {"port": 22, "transport": "tcp"} in out["open_ports"]
        assert out["banners"][0]["service"] == "ssh"


# ── Mock catalog ─────────────────────────────────────────────────────────


class TestMockCatalog:
    def test_default_is_deterministic(self) -> None:
        a = _load_mock_catalog("example.com")
        b = _load_mock_catalog("example.com")
        assert a == b
        assert {"port": 443, "transport": "tcp"} in a["open_ports"]
        assert a["certificates"][0]["subject_cn"] == "example.com"

    def test_catalog_file_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        catalog = {
            "target.test": {
                "open_ports": [{"port": 8080, "transport": "tcp"}],
                "dns_records": [{"type": "A", "name": "target.test", "value": "198.51.100.9"}],
            }
        }
        f = tmp_path / "catalog.json"
        f.write_text(json.dumps(catalog), encoding="utf-8")
        monkeypatch.setenv("DECEPTICON_OSINT_CATALOG", str(f))
        out = _load_mock_catalog("target.test")
        assert out["open_ports"] == [{"port": 8080, "transport": "tcp"}]
        assert out["dns_records"][0]["value"] == "198.51.100.9"

    def test_catalog_file_unusable_falls_back(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        f = tmp_path / "broken.json"
        f.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("DECEPTICON_OSINT_CATALOG", str(f))
        out = _load_mock_catalog("example.com")
        # Falls back to the deterministic synthesized catalog.
        assert {"port": 443, "transport": "tcp"} in out["open_ports"]


# ── Tool: offline / mock path ────────────────────────────────────────────


class TestToolOffline:
    async def test_no_credentials_uses_mock_catalog(self) -> None:
        raw = await osint_enrich.ainvoke({"domain": "example.com"})
        data = json.loads(raw)
        assert data["in_scope"] is True
        assert data["sources"] == ["mock"]
        assert data["open_ports"]
        assert data["certificates"]
        assert data["dns_records"]
        assert data["banners"]
        assert "errors" not in data

    async def test_normalizes_domain_in_output(self) -> None:
        raw = await osint_enrich.ainvoke({"domain": "https://example.com:443/x"})
        assert json.loads(raw)["domain"] == "example.com"

    async def test_empty_domain_errors(self) -> None:
        raw = await osint_enrich.ainvoke({"domain": "   "})
        assert json.loads(raw) == {"error": "no domain provided"}


# ── Tool: scope enforcement ──────────────────────────────────────────────


class TestToolScope:
    async def test_out_of_scope_refused_without_egress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DECEPTICON_OSINT_SCOPE", "*.example.com")

        def _explode(*_a: Any, **_k: Any) -> Any:
            raise AssertionError("no network egress allowed for out-of-scope target")

        monkeypatch.setattr(httpx, "AsyncClient", _explode)
        raw = await osint_enrich.ainvoke({"domain": "evil.test"})
        data = json.loads(raw)
        assert data["in_scope"] is False
        assert "out of scope" in data["error"]
        assert data["scope_patterns"] == ["*.example.com"]

    async def test_in_scope_wildcard_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DECEPTICON_OSINT_SCOPE", "*.example.com")
        raw = await osint_enrich.ainvoke({"domain": "api.example.com"})
        data = json.loads(raw)
        assert data["in_scope"] is True
        assert data["sources"] == ["mock"]

    async def test_refusal_is_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("DECEPTICON_OSINT_SCOPE", "example.com")
        # The "decepticon" root logger sets propagate=False, so let caplog's
        # handler see records by re-enabling propagation for the duration.
        monkeypatch.setattr(logging.getLogger("decepticon"), "propagate", True)
        with caplog.at_level("WARNING", logger="decepticon"):
            await osint_enrich.ainvoke({"domain": "evil.test"})
        assert any("out of target scope" in r.getMessage() for r in caplog.records)


# ── Tool: live source path (mocked transport) ────────────────────────────


def _shodan_handler(request: httpx.Request) -> httpx.Response:
    if "/dns/domain/" in request.url.path:
        return httpx.Response(
            200,
            json={
                "domain": "example.com",
                "data": [{"subdomain": "www", "type": "A", "value": "203.0.113.5"}],
            },
        )
    return httpx.Response(
        200,
        json={
            "matches": [
                {
                    "ip_str": "203.0.113.5",
                    "port": 443,
                    "transport": "tcp",
                    "data": "HTTP/1.1 200 OK",
                    "ssl": {
                        "cert": {"subject": {"CN": "example.com"}, "fingerprint": {"sha256": "abc"}}
                    },
                }
            ]
        },
    )


class TestToolLive:
    async def test_shodan_live_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHODAN_API_KEY", "secret")
        _install_async_transport(monkeypatch, _shodan_handler)
        raw = await osint_enrich.ainvoke({"domain": "example.com"})
        data = json.loads(raw)
        assert data["sources"] == ["shodan"]
        assert {"port": 443, "transport": "tcp"} in data["open_ports"]
        assert data["certificates"][0]["subject_cn"] == "example.com"
        assert data["dns_records"][0]["value"] == "203.0.113.5"
        assert "errors" not in data

    async def test_live_failure_captured_and_falls_back_to_mock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHODAN_API_KEY", "secret")

        def _fail(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        _install_async_transport(monkeypatch, _fail)
        raw = await osint_enrich.ainvoke({"domain": "example.com"})
        data = json.loads(raw)
        # Shodan errored, so no live source succeeded -> mock fallback.
        assert data["sources"] == ["mock"]
        assert data["errors"]
        assert "shodan" in data["errors"][0]

    async def test_fetch_shodan_direct(self) -> None:
        transport = httpx.MockTransport(_shodan_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            out = await osint._fetch_shodan(client, "example.com", "key")
        assert {"port": 443, "transport": "tcp"} in out["open_ports"]
        assert out["dns_records"][0]["value"] == "203.0.113.5"
