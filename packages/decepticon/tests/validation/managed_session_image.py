"""Opt-in canonical-image IO check. This does NOT qualify an OpenShell kernel.

uv run --extra openshell python packages/decepticon/tests/validation/managed_session_image.py \
  --image sha256:<locally-built-image-id> --report /private/new-report.json
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from decepticon.backends import openshell_local
from decepticon.backends.openshell_sandbox import OpenShellAdmission, OpenShellSandbox
from decepticon.sandbox_server.session_client import MANAGED_BRIDGE, MANAGED_START

ROE = b'{"machine_enforcement":{"in_scope":["example.invalid"],"mode":"enforce"}}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        parser.error("An exact locally-built immutable image ID is required")
    os.umask(0o077)
    report = {
        "image": args.image,
        "nativeOpenShell": False,
        "admission": "synthetic local transport fixture only",
        "productionEquivalent": False,
        "productionChanged": False,
        "ownedContainersRemoved": [],
    }
    nonce = uuid.uuid4().hex
    started = time.monotonic()
    output = args.report.open("x")
    traces = args.report.with_name(args.report.name + ".diagnostics.private.jsonl").open("x")

    def run(command, **kwargs):
        return subprocess.run(command, capture_output=True, timeout=45, **kwargs)

    class Session:
        id = "synthetic-owned-fixture"

        def __init__(self, container):
            self.container = container

        def exec(self, command, *, stdin=None, timeout_seconds=None, no_login_shell=False):
            value = run(["docker", "exec", "-i", self.container, *command], input=stdin)
            traces.write(
                json.dumps(
                    {
                        "command": command,
                        "exitCode": value.returncode,
                        "stderr": value.stderr.decode(errors="replace")[-12000:],
                        "stdoutTail": value.stdout.decode(errors="replace")[-2000:],
                    }
                )
                + "\n"
            )
            traces.flush()
            return SimpleNamespace(
                exit_code=value.returncode,
                stdout=value.stdout.decode(errors="replace"),
                stderr=value.stderr.decode(errors="replace"),
            )

    @contextmanager
    def workload():
        name = "dcpt-managed-" + uuid.uuid4().hex[:12]
        value = run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--init",
                "--label",
                "com.decepticon.qualification=" + nonce,
                "--network",
                "none",
                "--read-only",
                "--user",
                "1000:1000",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "1g",
                "--cpus",
                "1",
                "--tmpfs",
                "/workspace:rw,uid=1000,gid=1000,mode=1777,size=256m",
                "--tmpfs",
                "/tmp:rw,uid=1000,gid=1000,mode=1777,size=128m",
                "--entrypoint",
                "/bin/sleep",
                args.image,
                "600",
            ]
        )
        assert value.returncode == 0, value.stderr.decode(errors="replace")
        container = value.stdout.decode().strip()
        try:
            yield Session(container)
        finally:
            inspected = json.loads(run(["docker", "inspect", container]).stdout)[0]
            assert inspected["Name"] == "/" + name
            assert inspected["Config"]["Labels"]["com.decepticon.qualification"] == nonce
            removed = run(["docker", "rm", "-f", container])
            report["ownedContainersRemoved"].append(removed.returncode == 0)
            assert removed.returncode == 0

    def backend(session):
        receipt = OpenShellAdmission(
            binding_id="synthetic",
            sandbox_id=session.id,
            workspace="owned-fixture",
            runtime_generation="synthetic",
            approval_hash=hashlib.sha256(ROE).hexdigest(),
            effective_policy_hash="synthetic-not-kernel",
            expires_at=time.time() + 600,
            ready=True,
        )
        return OpenShellSandbox(session, admission=receipt, current_admission=lambda: receipt)

    def identity(session):
        value = session.exec(
            ["/usr/bin/python3", "-I", MANAGED_START],
            stdin=json.dumps(
                {
                    "roe_b64": base64.b64encode(ROE).decode(),
                    "roe_sha256": hashlib.sha256(ROE).hexdigest(),
                }
            ).encode(),
        )
        assert value.exit_code == 0, value.stderr
        return json.loads(value.stdout)

    try:
        with workload() as session:
            # All writable Python shadow candidates must be ignored by -I and
            # the explicitly inserted immutable package root.
            poison = session.exec(
                [
                    "/bin/sh",
                    "-c",
                    "; ".join(
                        [
                            "mkdir -p /workspace/decepticon",
                            "printf '%s\\n' 'raise RuntimeError(\"workspace shadow imported\")' > /workspace/json.py",
                            "printf '%s\\n' 'raise RuntimeError(\"workspace shadow imported\")' > /workspace/decepticon/__init__.py",
                        ]
                    ),
                ]
            )
            assert poison.exit_code == 0
            cold = time.monotonic()
            openshell_local._wait_for_session_daemon(session, approved_roe=ROE)
            report["coldDaemonReadySeconds"] = round(time.monotonic() - cold, 3)
            client = backend(session)
            execution = client.execute("printf managed-owned; id -u", timeout=10)
            assert (
                execution.exit_code == 0
                and "managed-owned" in execution.output
                and "1000" in execution.output
            )
            assert (
                client.upload_files([("/workspace/retained.txt", b"retained-owned-data")])[0].error
                is None
            )
            files = client.download_files(["/workspace/retained.txt", "/workspace/plan/roe.json"])
            assert files[0].content == b"retained-owned-data" and files[1].content == ROE
            assert "managed-tmux-owned" in client.execute_tmux(
                "printf managed-tmux-owned", session="fixture", timeout=10
            )
            first = identity(session)
            assert first["reused"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                rows = list(pool.map(lambda _: identity(session), range(8)))
            assert all(
                row["pid"] == first["pid"] and row["start"] == first["start"] and row["reused"]
                for row in rows
            )
            assert (
                client.download_files(["/workspace/retained.txt"])[0].content
                == b"retained-owned-data"
            )
            assert (
                client.execute(
                    "test ! -w /usr/local/lib/decepticon-sandbox/decepticon/sandbox_server/session_client.py",
                    timeout=10,
                ).exit_code
                == 0
            )
            report.update(
                realExecuteUid1000=True,
                realTmux=True,
                scopeAndFileRoundtrip=True,
                concurrentReattaches=8,
                sameProcessAndDataRetained=True,
                workspaceShadowIgnored=True,
                runtimeBundleReadOnly=True,
            )
        with workload() as session:
            # Deliberately occupy the port with the legacy daemon. Never kill
            # or accept that service as the newly bootstrapped managed process.
            legacy = session.exec(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "import subprocess,os; e=dict(os.environ); e.update(PYTHONPATH='/opt',DECEPTICON_SKIP_BOOT='1',SANDBOX_DAEMON_HOST='127.0.0.1',SANDBOX_ROOT_DIR='/workspace'); e.pop('DECEPTICON_MANAGED_SESSION',None); f=open('/tmp/legacy.log','ab'); p=subprocess.Popen(['/usr/bin/python3','-m','decepticon.sandbox_server'],env=e,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True); print(p.pid)",
                ]
            )
            assert legacy.exit_code == 0, legacy.stderr
            deadline = time.monotonic() + 15
            health = None
            while time.monotonic() < deadline:
                value = session.exec(
                    ["/usr/bin/python3", "-I", MANAGED_BRIDGE],
                    stdin=b'{"path":"/healthz","timeout":1}',
                )
                if value.exit_code == 0:
                    health = json.loads(value.stdout)
                    break
                time.sleep(0.2)
            assert health == {"status": 200, "body": {"status": "ok"}}
            old_timeout = openshell_local._DAEMON_READY_TIMEOUT_SECONDS
            openshell_local._DAEMON_READY_TIMEOUT_SECONDS = 3
            try:
                try:
                    openshell_local._wait_for_session_daemon(session, approved_roe=ROE)
                except RuntimeError as error:
                    assert "did not become ready" in str(error)
                else:
                    raise AssertionError("Legacy HTTP200 was accepted as managed readiness")
            finally:
                openshell_local._DAEMON_READY_TIMEOUT_SECONDS = old_timeout
            value = session.exec(
                ["/usr/bin/python3", "-I", MANAGED_BRIDGE], stdin=b'{"path":"/healthz","timeout":1}'
            )
            assert value.exit_code == 0 and json.loads(value.stdout) == health
            report["legacyHttp200RejectedWithoutKillingLegacyService"] = True
        report["passed"] = True
    except BaseException as error:
        report.update(passed=False, errorType=type(error).__name__, error=str(error)[:700])
        raise
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        json.dump(report, output, indent=2)
        output.close()
        traces.close()
        print(json.dumps(report))


if __name__ == "__main__":
    main()
