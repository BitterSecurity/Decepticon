"""Unit tests for the SSRF / safety classifier."""

from __future__ import annotations

from decepticon_sandbox.web import safety


def test_ip_blocked_internal_ranges() -> None:
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "0.0.0.0"):
        assert safety._ip_blocked(ip) is True, ip


def test_ip_blocked_public_allowed() -> None:
    for ip in ("8.8.8.8", "20.200.245.245", "2606:4700::6810:b22"):
        assert safety._ip_blocked(ip) is False, ip


def test_ipv4_mapped_private_is_blocked() -> None:
    # ::ffff:127.0.0.1 must not slip past the v6 checks.
    assert safety._ip_blocked("::ffff:127.0.0.1") is True
    assert safety._ip_blocked("::ffff:192.168.0.1") is True


def test_nat64_embedded_v4_decides() -> None:
    # 64:ff9b::<v4> (DNS64) must be judged by the EMBEDDED IPv4, not the prefix.
    assert safety._ip_blocked("64:ff9b::14c8:f5f5") is False  # 20.200.245.245 (public)
    assert safety._ip_blocked("64:ff9b::c0a8:0101") is True  # 192.168.1.1 (private)
    assert safety._ip_blocked("64:ff9b::7f00:0001") is True  # 127.0.0.1 (loopback)


def test_classify_url_scheme_and_ip_literals() -> None:
    assert safety.classify_url("ftp://example.com/x")[0] is False
    assert safety.classify_url("https://8.8.8.8/x")[0] is True
    assert safety.classify_url("https://127.0.0.1/x")[0] is False
    assert safety.classify_url("https://169.254.169.254/latest/meta-data/")[0] is False
    # allow_private bypass (local testing opt-in).
    assert safety.classify_url("https://127.0.0.1/x", allow_private=True)[0] is True
