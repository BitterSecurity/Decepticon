from __future__ import annotations

from typing import Any

import httpx
import pytest

from decepticon.backends.http_transport import HTTPSandbox, SandboxError, _retry_on_connection_error


def _make_handler(status: int = 200, body: dict[str, Any] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {})

    return handler


def _inject_client(sb: HTTPSandbox, handler: Any) -> None:
    sb._client = httpx.Client(
        base_url=sb._base_url,
        transport=httpx.MockTransport(handler),
    )


def test_id_property_strips_trailing_slash() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999/")
    assert sb.id == "http-sandbox:http://localhost:9999"


def test_id_property_no_trailing_slash() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    assert sb.id == "http-sandbox:http://localhost:9999"


def test_http_lazy_client_created_once_and_cached() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    c1 = sb._http()
    c2 = sb._http()
    assert c1 is c2
    assert sb._client is not None
    assert "decepticon-http-sandbox/1" in c1.headers["user-agent"]
    assert "authorization" not in c1.headers


def test_http_sets_bearer_header_when_token_provided() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999", token="secret")
    client = sb._http()
    assert client.headers["authorization"] == "Bearer secret"


def test_http_no_authorization_header_when_token_is_none() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999", token=None)
    client = sb._http()
    assert "authorization" not in client.headers


def test_close_idempotent_first_call_clears_client() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    sb._http()
    assert sb._client is not None
    sb.close()
    assert sb._client is None


def test_close_idempotent_second_call_does_not_raise() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    sb._http()
    sb.close()
    sb.close()
    assert sb._client is None


def test_close_when_no_client_initialized_does_not_raise() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    assert sb._client is None
    sb.close()


def test_retry_succeeds_first_try_no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    result = _retry_on_connection_error(lambda: "ok")
    assert result == "ok"
    assert sleep_calls == []


def test_retry_recovers_after_two_transient_connect_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    sentinel = object()
    attempts: list[int] = []

    def flaky() -> object:
        attempts.append(1)
        if len(attempts) <= 2:
            raise httpx.ConnectError("refused")
        return sentinel

    result = _retry_on_connection_error(flaky)
    assert result is sentinel
    assert sleep_calls == [0.5, 1.0]


def test_retry_exhausts_reraises_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("decepticon.backends.http_transport.time.sleep", lambda d: None)

    def always_fail() -> None:
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(httpx.ConnectTimeout):
        _retry_on_connection_error(always_fail)


def test_retry_rides_out_sandbox_boot_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-engagement sandbox can refuse connections for 1-2 min while it
    boots. The retry budget must outlast that window — far more than the old
    3-attempt cap — so the agent's first tool call survives the boot."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )
    sentinel = object()
    attempts: list[int] = []

    def booting() -> object:
        attempts.append(1)
        # Refuse for the first 15 attempts (would blow the old max_retries=3).
        if len(attempts) <= 15:
            raise httpx.ConnectError("[Errno 111] Connection refused")
        return sentinel

    result = _retry_on_connection_error(booting)
    assert result is sentinel
    assert len(attempts) == 16
    # Backoff is capped (no unbounded growth) so the loop keeps probing.
    assert max(sleep_calls) <= 8.0


def test_retry_budget_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely dead sandbox must still surface within the time budget —
    the cumulative (intended) backoff stays within max_total_delay."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "decepticon.backends.http_transport.time.sleep", lambda d: sleep_calls.append(d)
    )

    def always_fail() -> None:
        raise httpx.ConnectError("refused")

    with pytest.raises(httpx.ConnectError):
        _retry_on_connection_error(always_fail, max_total_delay=30.0, max_delay=8.0)
    assert sum(sleep_calls) <= 30.0
    assert max(sleep_calls) <= 8.0


def test_request_wraps_http_status_error_as_sandbox_error() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, _make_handler(500, None))
    with pytest.raises(SandboxError) as exc_info:
        sb._request("post", "/x", json={})
    assert "500" in str(exc_info.value)


def test_request_long_body_truncated_in_sandbox_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="e" * 500)

    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, handler)
    with pytest.raises(SandboxError) as exc_info:
        sb._request("post", "/x", json={})
    msg = str(exc_info.value)
    assert "503" in msg
    assert len(msg) < 1000


def test_request_success_returns_response() -> None:
    sb = HTTPSandbox(base_url="http://localhost:9999")
    _inject_client(sb, _make_handler(200, {"ok": 1}))
    resp = sb._request("get", "/y")
    assert resp.status_code == 200
    assert resp.json() == {"ok": 1}


# ── The daemon API route semantics live in test_daemon_client.py ──────
# They run against FakeDaemonTransport (the abstract _request seam) and
# are shared by every transport implementation. This file owns only the
# HTTP wire: pooling, headers, retry, and error translation.
