"""Replay input must fail before Docker or a paid provider can be reached."""

import importlib.util
import json
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    path = Path(__file__).with_name("run.py")
    spec = importlib.util.spec_from_file_location("agent_parity_replay_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.syspath_prepend(str(path.parents[2] / "config"))
    from config import codex_chatgpt_handler

    original = codex_chatgpt_handler._request_body
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        codex_chatgpt_handler._request_body = original


@pytest.mark.parametrize(
    "trace",
    [
        {"qualified": True, "mode": "live", "model_calls": []},
        {"qualified": False, "mode": "live", "model_calls": [{}]},
        {"qualified": True, "mode": "replay", "model_calls": [{}]},
        {"qualified": True, "mode": "live", "model_calls": {}},
        [],
    ],
)
def test_invalid_replay_stops_before_docker_or_provider(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    trace: object,
) -> None:
    source = tmp_path / "trace.json"
    source.write_text(json.dumps(trace))
    docker = Mock(side_effect=AssertionError("Docker must not start"))
    provider = Mock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(runner.subprocess, "check_output", docker)
    monkeypatch.setattr(runner.PROVIDER, "completion", provider)
    monkeypatch.setattr(
        runner.sys,
        "argv",
        [
            "run.py",
            "--replay",
            str(source),
            "--work-dir",
            str(tmp_path / "work"),
            "--output",
            str(tmp_path / "result.json"),
        ],
    )
    with pytest.raises(ValueError, match="qualified nonempty live trace"):
        runner.main()
    docker.assert_not_called()
    provider.assert_not_called()
    assert not (tmp_path / "work").exists()


def test_replay_http_request_never_falls_back_to_live_provider(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = Mock(side_effect=AssertionError("Provider must not be called"))
    monkeypatch.setattr(runner.PROVIDER, "completion", provider)
    monkeypatch.setattr(runner, "REPLAY_MODE", True, raising=False)
    server = runner.ThreadingHTTPServer(("127.0.0.1", 0), runner.Relay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        client.request(
            "POST",
            "/v1/chat/completions",
            json.dumps(
                {
                    "model": "local-replay-test",
                    "messages": [{"role": "user", "content": "local"}],
                    "tools": [],
                }
            ),
            {"Content-Type": "application/json"},
        )
        response = client.getresponse()
        assert response.status == 502
        response.read()
        provider.assert_not_called()
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
