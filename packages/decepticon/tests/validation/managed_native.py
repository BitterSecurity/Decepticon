"""Opt-in real local OpenShell resolver/daemon qualification; no model calls.

Supply a dedicated, disposable mTLS gateway and exact local sandbox image ID.
This tests OSS local admission, not SaaS tenancy or E2B kernel compatibility.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from openshell import SandboxClient, TlsConfig
from openshell._proto import openshell_pb2 as pb

from decepticon.backends import openshell_local
from decepticon.backends.daemon_client import SandboxError
from decepticon.middleware.policy_compiler import (
    compile_openshell_policy,
    sandbox_binding_digest,
    sandbox_name,
)
from decepticon.middleware.roe import _load_rules_for_workspace
from decepticon.sandbox_server.session_client import MANAGED_START


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--tls-dir", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--fixture-address", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--browser", action="store_true", help="Qualify actual desktop Node template"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"127\.0\.0\.1:[0-9]{1,5}", args.endpoint):
        parser.error("A dedicated loopback gateway is required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image):
        parser.error("An immutable local sandbox image ID is required")
    address = ipaddress.ip_address(args.fixture_address)
    if address.version != 4 or not address.is_private or address.is_loopback:
        parser.error("Use the owned Docker bridge IPv4 address for fixture listeners")
    os.umask(0o077)
    args.state_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    output = args.report.open("x")
    report = {
        "nativeOpenShell": True,
        "syntheticAdmission": False,
        "trustedClientInjection": "Real mTLS SDK client supplied to OSS local resolver",
        "saasTenantQualified": False,
        "e2bQualified": False,
        "productionEquivalent": False,
        "modelCalls": 0,
        "externalTargetRequests": 0,
        "image": args.image,
        "passed": False,
        "cleanedSandboxes": [],
        "checks": {},
    }
    start = time.monotonic()
    hits = {"allowed": 0, "denied": 0}

    def listener(kind):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits[kind] += 1
                self.send_response(200)
                if args.browser and kind == "allowed" and self.path == "/browser":
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                if args.browser and kind == "allowed" and self.path == "/browser":
                    body = (
                        '<output id="proof">owned browser fixture</output><script>'
                        'const count=Number(localStorage.getItem("visits")||0)+1;'
                        'localStorage.setItem("visits",String(count));'
                        'document.cookie="native_cookie=retained;path=/;max-age=600";'
                        'document.querySelector("#proof").setAttribute("data-visits",String(count));'
                        'fetch("http://host.docker.internal:'
                        + str(denied.server_port)
                        + '/browser-denied",{signal:AbortSignal.timeout(1500)}).then(()=>{'
                        'document.querySelector("#proof").setAttribute("data-network","unexpected");'
                        "}).catch(()=>{"
                        'document.querySelector("#proof").setAttribute("data-network","denied");});'
                        "</script>"
                    ).encode()
                    self.wfile.write(body)
                else:
                    self.wfile.write(b"owned-native-fixture")

            def log_message(self, format, *values):
                pass

        server = ThreadingHTTPServer((args.fixture_address, 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    servers = []
    owned = {}
    backends = []
    client = None
    original_client = openshell_local._client
    saved_env = {
        k: os.environ.get(k)
        for k in (
            "DECEPTICON_OPENSHELL_IMAGE",
            "DECEPTICON_OPENSHELL_ROE_PORTS",
            "DECEPTICON_OPENSHELL_WORKSPACE",
        )
    }
    try:
        allowed, denied = listener("allowed"), listener("denied")
        servers.extend((allowed, denied))
        tls = args.tls_dir
        client = SandboxClient(
            args.endpoint,
            timeout=30,
            tls=TlsConfig(
                tls / "ca.crt",
                tls / "client/tls.crt",
                tls / "client/tls.key",
            ),
        )
        openshell_local._client = lambda: client
        os.environ.update(
            DECEPTICON_OPENSHELL_IMAGE=args.image,
            DECEPTICON_OPENSHELL_WORKSPACE="default",
            DECEPTICON_OPENSHELL_ROE_PORTS=str(allowed.server_port),
        )
        roe = json.dumps(
            {
                "machine_enforcement": {
                    "in_scope": ["host.docker.internal"],
                    "mode": "enforce",
                }
            },
            sort_keys=True,
        ).encode()
        for label in ("a", "b"):
            workspace = args.state_dir / label
            (workspace / "plan").mkdir(parents=True)
            (workspace / "plan/roe.json").write_bytes(roe)
            compiled = compile_openshell_policy(_load_rules_for_workspace(str(workspace)))
            assert not compiled.errors, compiled.errors
            approval = hashlib.sha256(roe).hexdigest()
            name = sandbox_name(str(workspace), compiled.canonical_hash, approval_hash=approval)
            digest = sandbox_binding_digest(
                str(workspace),
                compiled.canonical_hash,
                approval_hash=approval,
            )
            # Never adopt/delete an existing resource as a fixture.
            try:
                client.get(name, workspace="default")
            except Exception as exc:
                if "not found" not in str(exc).lower() and "NOT_FOUND" not in str(exc):
                    raise
            else:
                raise RuntimeError("Fixture sandbox already exists")
            owned[name] = digest
            backend = openshell_local.resolve_local_binding(
                {
                    "configurable": {"workspace_path": str(workspace)},
                }
            )
            backends.append(backend)
        a, b = backends
        checks = report["checks"]
        checks["differentRuntimeIds"] = a.id != b.id
        assert checks["differentRuntimeIds"]
        identity = a.execute("id -u; uname -r", timeout=10)
        assert identity.exit_code == 0 and identity.output.splitlines()[0] == "1000"
        checks["identity"] = identity.output
        assert a.upload_files([("/workspace/retained", b"owned-a")])[0].error is None
        assert a.download_files(["/workspace/retained"])[0].content == b"owned-a"
        assert b.download_files(["/workspace/retained"])[0].content is None
        assert a.download_files(["/workspace/plan/roe.json"])[0].content == roe
        checks["scopeAndRuntimeFilesIsolated"] = True
        assert "kept:/tmp" in a.execute_tmux(
            'export NATIVE_STATE=kept; cd /tmp; printf "%s:%s" "$NATIVE_STATE" "$PWD"',
            session="persistent",
            timeout=10,
        )
        assert "unset" in a.execute_tmux(
            'printf "%s" "${NATIVE_STATE:-unset}"',
            session="separate",
            timeout=10,
        )
        checks["tmuxStateAndSessionIsolation"] = True
        good = a.execute(
            f"curl -fsS --max-time 5 http://host.docker.internal:{allowed.server_port}", timeout=10
        )
        bad = a.execute(
            f"curl -fsS --max-time 5 http://host.docker.internal:{denied.server_port}", timeout=10
        )
        checks["ownedNetworkResults"] = {
            "allowed": {"exitCode": good.exit_code, "output": good.output},
            "denied": {"exitCode": bad.exit_code, "output": bad.output},
            "listenerHits": dict(hits),
        }
        assert good.exit_code == 0 and "owned-native-fixture" in good.output
        assert bad.exit_code != 0 and hits["allowed"] > 0 and hits["denied"] == 0
        checks["ownedNetworkAllowDeny"] = True
        checks["ownedListenerHits"] = dict(hits)
        if args.browser:
            rows = []
            for visit in (1, 2):
                payload = json.dumps(
                    {
                        "url": f"http://host.docker.internal:{allowed.server_port}/browser",
                        "profileDir": "/workspace/native-chrome-profile",
                        "waitSelector": "#proof[data-visits]",
                        "headless": True,
                        "allowPrivate": True,  # Owned fixture; runtime policy must still deny.
                        "timeout": 15000,
                    }
                )
                wire = base64.b64encode(payload.encode()).decode()
                started = time.monotonic()
                result = a.execute(
                    f"printf %s {shlex.quote(wire)} | base64 -d | node "
                    "/usr/local/lib/decepticon-sandbox/decepticon/sandbox_web/templates/"
                    "playwright_real_chrome.js",
                    timeout=40,
                )
                assert result.exit_code == 0, result.output
                # Daemon execution wraps stdout/stderr; parse only the JSON envelope line.
                envelopes = [
                    json.loads(line)
                    for line in result.output.splitlines()
                    if line.startswith('{"html":')
                ]
                assert len(envelopes) == 1, result.output
                envelope = envelopes[0]
                checks.setdefault("desktopBrowserObservations", []).append(envelope)
                assert envelope["status"] == 200
                assert f'data-visits="{visit}"' in envelope["html"]
                assert 'data-network="denied"' in envelope["html"]
                assert any(
                    cookie["name"] == "native_cookie" and cookie["value"] == "retained"
                    for cookie in envelope["cookies"]
                )
                rows.append(
                    {
                        "visit": visit,
                        "seconds": round(time.monotonic() - started, 3),
                        "automation": envelope["automation"],
                        "status": envelope["status"],
                    }
                )
            assert hits["denied"] == 0
            checks["desktopBrowser"] = {
                "runs": rows,
                "javascriptExecuted": True,
                "cookieReturned": True,
                "localStorageSurvivesContextRestart": True,
                "ownedDeniedListenerHits": hits["denied"],
            }
        audit = pb.GetSandboxLogsRequest(sandbox=next(iter(owned)), lines=500)
        audit.workspace_scope.workspace = "default"
        deadline = time.monotonic() + 10
        decisions = []
        logs = []
        while time.monotonic() < deadline:
            logs = list(client._stub.GetSandboxLogs(audit, timeout=10).logs)
            messages = [
                line.message
                for line in logs
                if line.source == "sandbox"
                and line.target == "ocsf"
                and line.sandbox_id == a._admission.sandbox_id
            ]
            # Transparent TCP denials name the policy's synthetic DNS address.
            # Bind that address to this exact sandbox/hostname/owned listener,
            # rather than accepting a timeout or an unrelated denial message.
            mapped = set()
            for message in messages:
                match = re.search(
                    r"Policy DNS mapped host\.docker\.internal resolved="
                    + re.escape(str(address))
                    + r" synthetic=([0-9.]+) ports="
                    + str(allowed.server_port)
                    + r"(?: |$)",
                    message,
                )
                if match:
                    mapped.add(str(ipaddress.IPv4Address(match.group(1))))
            decisions = [
                message
                for message in messages
                if message.startswith("NET:OPEN ")
                and " DENIED " in message
                and "[reason:transparent_tcp_mapping_denied]" in message
                and any(f"-> {ip}:{denied.server_port} " in message for ip in mapped)
            ]
            if decisions:
                break
            time.sleep(0.25)
        audit_file = args.report.with_suffix(".audit.private.json")
        with audit_file.open("x") as handle:
            json.dump(
                [
                    {
                        "source": line.source,
                        "target": line.target,
                        "sandbox_id": line.sandbox_id,
                        "message": line.message,
                    }
                    for line in logs
                ],
                handle,
            )
        assert decisions, "Missing native Supervisor policy-denial evidence"
        checks["supervisorPolicyDenialObserved"] = True
        checks["syntheticDnsMappingBoundToOwnedListener"] = True

        def reattach(_):
            wire = json.dumps(
                {
                    "roe_b64": base64.b64encode(roe).decode(),
                    "roe_sha256": hashlib.sha256(roe).hexdigest(),
                }
            ).encode()
            result = a._session.exec(
                ["/usr/bin/python3", "-I", MANAGED_START],
                stdin=wire,
                timeout_seconds=15,
                no_login_shell=True,
            )
            assert result.exit_code == 0, result.stderr
            return json.loads(result.stdout)

        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(reattach, range(4)))
        assert all(
            r["reused"] and r["pid"] == rows[0]["pid"] and r["start"] == rows[0]["start"]
            for r in rows
        )
        assert a.download_files(["/workspace/retained"])[0].content == b"owned-a"
        checks["fourNativeReattachmentsKeepProcessAndData"] = True
        (args.state_dir / "a/plan/roe.json").write_bytes(roe + b"\n")
        try:
            a.execute("printf must-not-execute", timeout=10)
        except SandboxError:
            checks["changedApprovedBytesRefuseNewExecution"] = True
        else:
            raise AssertionError("Changed approval was accepted")
        report["passed"] = True
    except BaseException as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
        raise
    finally:
        for backend in backends:
            backend.close()
        for name, digest in owned.items():
            try:
                assert client is not None
                ref = client.get(name, workspace="default")
                assert ref.labels.get("decepticon-binding") == digest
                client.delete(name, workspace="default", allow_missing=False)
                client.wait_deleted(name, workspace="default", timeout_seconds=30)
                report["cleanedSandboxes"].append(name)
            except Exception as exc:
                report.setdefault("cleanupErrors", []).append(type(exc).__name__ + ": " + str(exc))
        openshell_local._client = original_client
        if client is not None:
            client.close()
        for server in servers:
            server.shutdown()
            server.server_close()
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        report["passed"] = report["passed"] and not report.get("cleanupErrors")
        report["seconds"] = round(time.monotonic() - start, 3)
        json.dump(report, output, indent=2)
        output.write("\n")
        output.close()
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
