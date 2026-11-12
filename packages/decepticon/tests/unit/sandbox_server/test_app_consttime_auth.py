from __future__ import annotations

import contextlib
import importlib
import os
import types
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

app_module = importlib.import_module("decepticon_sandbox.server.app")
app = app_module.app

TOKEN = "correct-token-value"


@pytest.fixture(autouse=True)
def _reset_globals() -> Iterator[None]:
    for name in ("_backend", "_required_token"):
        setattr(app_module, name, None)
    yield
    for name in ("_backend", "_required_token"):
        setattr(app_module, name, None)


def _make_backend() -> MagicMock:
    backend = MagicMock()
    backend.execute.return_value = types.SimpleNamespace(
        output="ok\n", exit_code=0, truncated=False
    )
    backend.kill_all_sessions.return_value = 0
    return backend


@contextlib.contextmanager
def _client(backend: MagicMock, *, token: str | None) -> Iterator[TestClient]:
    env: dict[str, str] = {} if token is None else {"SANDBOX_TOKEN": token}
    saved = os.environ.get("SANDBOX_TOKEN")
    with patch.object(app_module, "_get_backend", return_value=backend):
        os.environ.pop("SANDBOX_TOKEN", None)
        os.environ.update(env)
        try:
            with TestClient(app) as client:
                yield client
        finally:
            os.environ.pop("SANDBOX_TOKEN", None)
            if saved is not None:
                os.environ["SANDBOX_TOKEN"] = saved


def test_verify_token_uses_compare_digest() -> None:
    import hmac as _hmac

    verify = app_module._verify_token
    setattr(app_module, "_required_token", TOKEN)
    calls: list[tuple[str, str]] = []
    original = _hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return original(a, b)

    with patch.object(_hmac, "compare_digest", side_effect=spy):
        with contextlib.suppress(Exception):
            verify(f"Bearer {TOKEN}")

    assert len(calls) == 1, "hmac.compare_digest must be called for token comparison"
    assert calls[0] == (TOKEN, TOKEN)


def test_correct_token_grants_access() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post(
            "/execute",
            json={"command": "echo hi"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    assert resp.status_code == 200


def test_wrong_token_is_rejected_401() -> None:
    backend = _make_backend()
    with _client(backend, token=TOKEN) as client:
        resp = client.post(
            "/execute",
            json={"command": "echo hi"},
            headers={"Authorization": "Bearer wrong-value"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid token"
