"""Real Obscura provider round trip (no fakes).

Runs only where the Obscura binary is installed (the sandbox image, or a
developer machine with ``DECEPTICON_OBSCURA_BIN`` set). It starts a loopback
fixture site, drives the provider through a stateful flow and checks that the
browser server is torn down by ``shutdown``.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from decepticon_sandbox.browser.contract import BrowserRequest
from decepticon_sandbox.browser.providers.obscura import ObscuraProvider, _pid_alive

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DECEPTICON_OBSCURA_BIN") or shutil.which("obscura")),
    reason="obscura binary not installed",
)

_INDEX = """<!doctype html><html><head><title>Fixture Login</title></head><body>
<form id="f" action="/welcome.html" method="get">
<label>User <input name="user" id="user"></label>
<button id="go" type="submit">Sign in</button></form>
<div id="out"></div>
<script>
document.cookie = "fixture_session=abc123; path=/";
console.log("fixture loaded");
fetch("/api.json").then(r => r.json()).then(j => {
  document.getElementById("out").textContent = "api:" + j.status; });
</script></body></html>"""
_WELCOME = """<!doctype html><html><head><title>Welcome</title></head><body><h1 id="w"></h1>
<script>document.getElementById("w").textContent =
"Welcome " + new URLSearchParams(location.search).get("user");</script></body></html>"""


@pytest.fixture
def site(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "site"
    root.mkdir()
    (root / "index.html").write_text(_INDEX)
    (root / "welcome.html").write_text(_WELCOME)
    (root / "api.json").write_text('{"status":"ok"}')

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def log_message(self, format, *args):  # noqa: A002 - quiet fixture server
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def test_obscura_stateful_flow(tmp_path: Path, site: str) -> None:
    workspace = tmp_path / "workspace"
    provider = ObscuraProvider(workspace)

    def run(action: str, **args):
        result = provider.invoke(BrowserRequest(action, args).validate())
        assert result.ok, f"{action}: {result.error_code} {result.error}"
        return result

    try:
        run("navigate", url=f"{site}/index.html")
        snapshot = run("snapshot", max_chars=2000).text
        assert "Fixture Login" in snapshot and "ref=" in snapshot
        run("wait", text="api:ok", timeout_s=10)
        assert "fixture_session=abc123" in run("evaluate", expression="document.cookie").text
        assert "/api.json" in run("network_list").text
        assert "fixture loaded" in run("console_list").text

        saved = json.loads(run("storage_export", name="login").text)
        saved_path = Path(saved["saved_to"])
        assert saved_path.stat().st_mode & 0o077 == 0

        run("fill", selector="#user", value="alice")
        run("click", selector="#go")
        assert "Welcome alice" in run("markdown").text

        shot = run("screenshot", width=640, height=480)
        assert (
            shot.artifacts and Path(shot.artifacts[0].path).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        )

        bad = provider.invoke(BrowserRequest("click", {"ref": "e999"}))
        assert not bad.ok and bad.error_code == "action_failed"
    finally:
        state = json.loads((workspace / ".browser" / "obscura" / "server.json").read_text())
        provider.shutdown()
    assert not _pid_alive(state["pid"])
