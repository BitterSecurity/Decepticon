"""Engine SSRF guard defers to the OpenShell policy inside managed sessions."""

from __future__ import annotations

import pytest
from decepticon_sandbox.web import safety


def _resolve_to(monkeypatch: pytest.MonkeyPatch, ip: str) -> None:
    monkeypatch.setattr(
        safety.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", (ip, 80))],
    )


def test_policy_dns_synthetic_address_is_refused_without_kernel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECEPTICON_MANAGED_SESSION", raising=False)
    monkeypatch.delenv("INSANE_ALLOW_PRIVATE", raising=False)
    _resolve_to(monkeypatch, "198.18.0.2")
    ok, reason = safety.classify_url("http://target.example/", safety.allow_private_default())
    assert not ok and reason.startswith("resolves_internal")


def test_managed_session_defers_address_checks_to_openshell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DECEPTICON_MANAGED_SESSION", "1")
    monkeypatch.delenv("INSANE_ALLOW_PRIVATE", raising=False)
    _resolve_to(monkeypatch, "198.18.0.2")
    assert safety.kernel_enforced()
    assert safety.classify_url("http://target.example/", safety.allow_private_default())[0]


def test_managed_session_still_enforces_schemes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECEPTICON_MANAGED_SESSION", "1")
    ok, reason = safety.classify_url("file:///etc/passwd", safety.allow_private_default())
    assert not ok and reason.startswith("scheme:")


def test_cleartext_requests_avoid_the_h2c_upgrade() -> None:
    curl_cffi = pytest.importorskip("curl_cffi")  # installed in the sandbox image only
    CurlHttpVersion = curl_cffi.CurlHttpVersion

    assert safety.http_version_for("http://target.example/a") == CurlHttpVersion.V1_1
    assert safety.http_version_for("HTTP://target.example/a") == CurlHttpVersion.V1_1
    assert safety.http_version_for("https://target.example/a") is None
