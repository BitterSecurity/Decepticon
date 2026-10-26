"""Live stock OpenShell tests against a disposable local Docker gateway.

Set DECEPTICON_OPENSHELL_TEST_ENDPOINT and DECEPTICON_OPENSHELL_TEST_HOST_IP.
The HTTP listeners are owned by this test, never real engagement targets.
Fixture admission below tests the transport/session adapter, not production
custody, runtime-generation discovery or application policy-ACK verification.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

if not os.environ.get("DECEPTICON_OPENSHELL_TEST_ENDPOINT"):
    pytest.skip("explicit disposable OpenShell test gateway required", allow_module_level=True)

from langchain_nvidia_openshell import OpenShellSandbox as UpstreamBackend
from openshell import SandboxClient
from openshell._proto import openshell_pb2 as pb

from decepticon.backends.openshell_sandbox import OpenShellAdmission, OpenShellSandbox


@pytest.fixture(scope="module")
def live():
    address = os.environ["DECEPTICON_OPENSHELL_TEST_HOST_IP"]
    hits = {"allowed": 0, "denied": 0}

    def listener(kind):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits[kind] += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"controlled-fixture")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer((address, 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server

    allowed, denied = listener("allowed"), listener("denied")
    client = SandboxClient(os.environ["DECEPTICON_OPENSHELL_TEST_ENDPOINT"], timeout=30)
    name = "dcptn-" + uuid4().hex[:10]
    created = False
    try:
        spec = pb.SandboxSpec()
        spec.template.image = os.environ.get(
            "DECEPTICON_OPENSHELL_TEST_IMAGE", "decepticon-openshell-reference:20261026"
        )
        spec.policy.version = 1
        spec.policy.filesystem.include_workdir = True
        spec.policy.filesystem.read_only.extend(
            [
                "/bin",
                "/usr",
                "/lib",
                "/lib64",
                "/etc",
                "/proc",
                "/opt",
                "/dev/urandom",
                "/dev/random",
            ]
        )
        spec.policy.filesystem.read_write.extend(
            ["/workspace", "/tmp", "/run", "/dev/null", "/dev/pts"]
        )
        spec.policy.landlock.compatibility = "hard_requirement"
        spec.policy.process.run_as_user = "1500"
        spec.policy.process.run_as_group = "1500"
        rule = spec.policy.network_policies["controlled-fixture"]
        rule.name = "controlled-fixture"
        rule.binaries.add(path="/usr/bin/curl")
        # Hostless IP scopes use the forward proxy. Transparent TCP rules
        # require a DNS hostname and cannot represent this fixture.
        rule.endpoints.add(port=allowed.server_port, allowed_ips=[address + "/32"])
        ref = client.create(workspace="default", name=name, spec=spec)
        created = True
        ready = client.wait_ready(name, workspace="default", timeout_seconds=90)
        assert ready.current_policy_version >= 1
        session = client.get_session(name, workspace="default")
        # The managed runtime owns its entrypoint; image ENTRYPOINT/ENV are
        # not a daemon lifecycle contract. Start the retained session service
        # through the admitted OpenShell exec path with explicit environment.
        daemon = session.exec(
            [
                "/bin/sh",
                "-c",
                "mkdir -p /workspace/live-test; PYTHONPATH=/opt DECEPTICON_SKIP_BOOT=1 "
                "SANDBOX_ROOT_DIR=/workspace/live-test SANDBOX_DAEMON_HOST=127.0.0.1 "
                "/usr/bin/python3 -m decepticon.sandbox_server >/tmp/session-daemon.log 2>&1 </dev/null & "
                "sleep 1; cat /tmp/session-daemon.log",
            ],
            timeout_seconds=15,
            no_login_shell=True,
        )
        assert daemon.exit_code == 0, daemon.stderr
        deadline = time.monotonic() + 30
        while True:
            bridge = session.exec(
                ["/usr/bin/python3", "/opt/decepticon/sandbox_server/session_client.py"],
                stdin=json.dumps(
                    {"path": "/poll_completion", "body": {"session": "none"}}
                ).encode(),
                timeout_seconds=15,
                no_login_shell=True,
            )
            if bridge.exit_code == 0:
                break
            if time.monotonic() >= deadline:
                logs = session.exec(["/bin/cat", "/tmp/session-daemon.log"], no_login_shell=True)
                pytest.fail(logs.stdout + logs.stderr + bridge.stderr)
            time.sleep(0.5)
        receipt = OpenShellAdmission(
            binding_id="fixture-" + name,
            sandbox_id=ref.id,
            workspace="default",
            runtime_generation="fixture-generation",
            approval_hash="fixture-approval",
            effective_policy_hash="fixture-policy-v1",
            expires_at=time.time() + 600,
            ready=True,
        )
        backend = OpenShellSandbox(session, admission=receipt, current_admission=lambda: receipt)
        yield {
            "backend": backend,
            "upstream": UpstreamBackend(sandbox=session),
            "session": session,
            "hits": hits,
            "allowed": f"http://{address}:{allowed.server_port}/allowed",
            "denied": f"http://{address}:{denied.server_port}/denied",
        }
        backend.close()
    finally:
        if created:
            client.delete(name, workspace="default", allow_missing=True)
            client.wait_deleted(name, workspace="default", timeout_seconds=30)
        client.close()
        for server in (allowed, denied):
            server.shutdown()
            server.server_close()


def test_official_backend_execute_and_binary_empty_files(live):
    backend = live["upstream"]
    assert backend.execute("printf official-backend").output == "official-backend"
    files = [("/workspace/empty", b""), ("/workspace/binary", b"\x00\xff\n")]
    assert all(result.error is None for result in backend.upload_files(files))
    assert [result.content for result in backend.download_files([p for p, _ in files])] == [
        data for _, data in files
    ]


def test_policy_allows_only_the_controlled_endpoint(live):
    backend = live["upstream"]
    allowed = backend.execute(f"curl -fsS --max-time 3 {live['allowed']}")
    assert allowed.exit_code == 0
    assert "controlled-fixture" in allowed.output
    denied = backend.execute(f"curl -fsS --max-time 3 {live['denied']}")
    assert denied.exit_code != 0
    assert live["hits"]["allowed"] >= 1
    assert live["hits"]["denied"] == 0


def test_tmux_retains_environment_and_cwd(live):
    backend = live["backend"]
    backend.execute_tmux("export DCPTN_SESSION_CHECK=retained; cd /tmp", session="persist")
    output = backend.execute_tmux('printf "%s:%s" "$DCPTN_SESSION_CHECK" "$PWD"', session="persist")
    assert "retained:/tmp" in output


def test_tmux_sessions_are_independent(live):
    backend = live["backend"]
    backend.execute_tmux("export DCPTN_ISOLATION=first", session="first")
    assert "not-set" in backend.execute_tmux(
        'printf "%s" "${DCPTN_ISOLATION:-not-set}"', session="second"
    )


def test_tmux_network_request_cannot_bypass_policy(live):
    output = live["backend"].execute_tmux(
        f"curl -fsS --max-time 3 {live['denied']}; printf 'CURL_EXIT=%s' $?",
        session="network",
    )
    assert "CURL_EXIT=0" not in output
    assert "CURL_EXIT=" in output
    assert live["hits"]["denied"] == 0


def test_background_completion_and_incremental_log(live):
    backend = live["backend"]
    backend.start_background("sleep 0.2; printf background-finished", session="background")
    deadline = time.monotonic() + 15
    job = None
    while time.monotonic() < deadline:
        job = backend.poll_completion("background")
        if job and str(getattr(job.status, "value", job.status)) != "running":
            break
        time.sleep(0.2)
    assert job is not None and job.exit_code == 0
    output = ""
    deadline = time.monotonic() + 5
    while "background-finished" not in output and time.monotonic() < deadline:
        output += backend.read_session_log_diff("background")
        time.sleep(0.1)
    if "background-finished" not in output:
        diagnostic = live["upstream"].execute(
            "ls -la /workspace/.sessions; cat /tmp/session-daemon.log; "
            "tmux list-panes -a -F '#{session_name}:#{pane_pipe}'"
        )
        pytest.fail(diagnostic.output)
    assert "background-finished" not in backend.read_session_log_diff("background")


def test_async_tmux_uses_the_same_session_transport(live):
    output = asyncio.run(live["backend"].execute_tmux_async("printf async-ok", session="async"))
    assert "async-ok" in output


def test_kill_session_clears_the_job(live):
    backend = live["backend"]
    backend.start_background("sleep 30", session="kill")
    backend.kill_session("kill")
    assert backend.poll_completion("kill") is None


def test_interactive_input_reaches_the_existing_shell(live):
    backend = live["backend"]
    backend.execute_tmux(
        "read -p 'Password: ' value; printf 'received:%s' \"$value\"",
        session="interactive",
        timeout=1,
    )
    output = backend.execute_tmux("fixture-answer", session="interactive", is_input=True, timeout=3)
    assert "received:fixture-answer" in output
