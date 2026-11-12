"""Daemon imports stay lean; real agent transports retain their contracts."""

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from decepticon_sandbox.kernel.base import SandboxBase
from decepticon_sandbox.kernel.daemon import DaemonSandbox
from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse
from fastapi.testclient import TestClient

from decepticon.backends.http_transport import HTTPTransportSandbox


def test_fresh_daemon_import_refuses_any_agent_stack_dependency():
    root = Path(__file__).resolve().parents[5]
    code = """
import importlib.abc,json,os,sys
sys.path[:0]=json.loads(sys.argv[1])
class RefuseAgentStack(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'deepagents','langchain','langchain_core','langgraph','langsmith'}:
            raise AssertionError('Execution daemon imports agent stack: '+fullname)
sys.meta_path.insert(0,RefuseAgentStack())
import decepticon_sandbox.server.app
print('lean-daemon-imported')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            code,
            json.dumps([str(root / "packages/decepticon"), str(root / "packages/decepticon-core")]),
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lean-daemon-imported"


@pytest.mark.skipif(os.name == "nt", reason="Execution daemon runs in the POSIX sandbox")
def test_legacy_adapter_retains_real_deep_agents_execution_record():
    class LegacySandbox(SandboxBase):
        def upload_files(self, files):
            return []

        def download_files(self, paths):
            return []

    result = LegacySandbox().execute("printf legacy-kernel")
    assert isinstance(result, ExecuteResponse)
    assert result.output == "legacy-kernel" and result.exit_code == 0


@pytest.mark.skipif(os.name == "nt", reason="Execution daemon uses POSIX commands and paths")
def test_real_http_transport_preserves_files_and_agent_filesystem_helpers(tmp_path, monkeypatch):
    app = importlib.import_module("decepticon_sandbox.server.app")
    backend = DaemonSandbox()
    # This wire test owns no tmux sessions and must not reap another task's.
    monkeypatch.setattr(backend, "reap_orphaned_tmux_sessions", lambda: 0)
    monkeypatch.setattr(app, "_backend", backend)
    monkeypatch.setattr(app, "_required_token", None)
    monkeypatch.setenv("SANDBOX_TOKEN", "owned-wire-test-token")
    path = str(tmp_path / "roundtrip.txt")
    with TestClient(app.app, headers={"Authorization": "Bearer owned-wire-test-token"}) as client:
        transport = HTTPTransportSandbox("http://testserver")

        # Current Starlette uses httpx2. Bridge the in-process ASGI response
        # back to the agent transport's httpx types; execute real route/kernel IO.
        def bridge(request: httpx.Request) -> httpx.Response:
            response = client.request(
                request.method,
                request.url.path,
                content=request.content,
                headers=dict(request.headers),
            )
            return httpx.Response(
                response.status_code, content=response.content, headers=dict(response.headers)
            )

        transport._client = httpx.Client(
            base_url="http://testserver", transport=httpx.MockTransport(bridge)
        )
        executed = transport.execute("printf owned-kernel-wire", timeout=5)
        assert isinstance(executed, ExecuteResponse)
        assert executed.output == "owned-kernel-wire" and executed.exit_code == 0
        uploaded = transport.upload_files([(path, b"first line\nsecond line")])
        assert isinstance(uploaded[0], FileUploadResponse) and uploaded[0].error is None
        downloaded = transport.download_files([path, str(tmp_path / "missing")])
        assert isinstance(downloaded[0], FileDownloadResponse)
        assert downloaded[0].content == b"first line\nsecond line"
        assert downloaded[1].content is None and downloaded[1].error == "file_not_found"
        read = transport.read(path, offset=1, limit=1)
        assert read.error is None and read.file_data is not None
        assert read.file_data["content"] == "second line"
        edited = transport.edit(path, "second line", "edited line")
        assert edited.error is None
        assert Path(path).read_text() == "first line\nedited line"
        client.headers.pop("Authorization")
        assert client.post("/execute", json={"command": "printf forbidden"}).status_code == 401
        transport.close()
