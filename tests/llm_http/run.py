"""Measure direct live LLM HTTP calls and an actual-response HTTP replay."""

import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BINARY = HERE / "rust/target/release/llm-http-comparison"
NAMES = ["langchain", "python", "native"]
EXPECTED = json.loads((HERE / "scenario.json").read_text())["expected"]


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def os_snapshot(pid: int) -> dict[str, Any]:
    proc = Path(f"/proc/{pid}")
    status = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines())
    smaps = dict(line.split(":", 1) for line in (proc / "smaps_rollup").read_text().splitlines())
    sockets = set()
    for fd in (proc / "fd").iterdir():
        try:
            target = fd.readlink().as_posix()
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            sockets.add(target[8:-1])
    tcp = []
    for name in ("tcp", "tcp6"):
        for line in (proc / "net" / name).read_text().splitlines()[1:]:
            fields = line.split()
            if fields[9] in sockets:
                tcp.append(
                    {
                        "inode": fields[9],
                        "state": fields[3],
                        "local_port": int(fields[1].rsplit(":", 1)[1], 16),
                        "remote_port": int(fields[2].rsplit(":", 1)[1], 16),
                    }
                )
    return {
        "affinity": status["Cpus_allowed_list"].strip(),
        "pss_kib": int(smaps["Pss"].split()[0]),
        "tcp": tcp,
        "io": dict(
            (k, int(v))
            for k, v in (line.split(":", 1) for line in (proc / "io").read_text().splitlines())
        ),
        "context_switches": {
            k: int(status[k]) for k in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches")
        },
    }


async def start_server(
    name: str,
    base: str,
    token: str,
    headers: dict[str, str],
    root: Path,
    cpu: int,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    port = free_port()
    command = (
        [sys.executable, str(HERE / "python_gateway.py"), name, str(port)]
        if name in {"langchain", "python"}
        else [str(BINARY), name, str(port), str(HERE)]
    )
    env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG"}}
    env.update(
        BENCH_BASE=base,
        BENCH_TOKEN=token,
        BENCH_HEADERS=json.dumps(headers),
        LANGSMITH_TRACING="false",
        PYTHONHASHSEED="0",
    )
    log_path = root / f"{name}-{port}.stderr"
    started = time.monotonic()
    with log_path.open("w") as stderr:
        process = subprocess.Popen(
            ["taskset", "-c", str(cpu), *command], env=env, stdout=stderr, stderr=stderr
        )
    url = f"http://127.0.0.1:{port}"
    try:
        while time.monotonic() - started < 120:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited; inspect {log_path}")
            try:
                response = await client.get(url + "/metrics")
                response.raise_for_status()
                ready = response.json()
                ready["os"] = os_snapshot(process.pid)
                return {
                    "process": process,
                    "url": url,
                    "name": name,
                    "startup_ms": (time.monotonic() - started) * 1000,
                    "ready": ready,
                }
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
        raise TimeoutError(f"{name} not ready")
    except BaseException:
        process.kill()
        process.wait()
        raise


def stop_server(server: dict[str, Any]) -> None:
    process = server["process"]
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


async def call(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    started = time.monotonic()
    text = ""
    first = None
    done = False
    error = None
    deltas = []
    delta_times = []
    wire_bytes = 0
    terminal_ms = None
    model_name = None
    usage = {}
    async with client.stream("POST", url + "/invoke", content=b"") as response:
        response.raise_for_status()
        headers_ms = (time.monotonic() - started) * 1000
        async for line in response.aiter_lines():
            wire_bytes += len(line.encode()) + 1
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            if payload.get("delta"):
                if first is None:
                    first = (time.monotonic() - started) * 1000
                delta_times.append((time.monotonic() - started) * 1000)
                text += payload["delta"]
                deltas.append(payload["delta"])
            if payload.get("done"):
                done = True
                terminal_ms = (time.monotonic() - started) * 1000
                model_name = payload.get("model")
                usage = payload.get("usage")
                if payload["text"] != text:
                    raise AssertionError("stream and final text differ")
            if payload.get("error"):
                error = payload["error"]
    return {
        "ttfb_ms": headers_ms,
        "delta_times_ms": delta_times,
        "terminal_ms": terminal_ms,
        "sse_payload_bytes": wire_bytes,
        "http_status": response.status_code,
        "ttft_ms": first,
        "wall_ms": (time.monotonic() - started) * 1000,
        "text": text,
        "deltas": deltas,
        "completed": done,
        "error": error,
        "qualified": done and error is None and text == EXPECTED and model_name == "gpt-6.1-sol",
        "model": model_name,
        "usage": usage,
    }


async def batch(client: httpx.AsyncClient, url: str, concurrency: int) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one() -> dict[str, Any]:
        async with semaphore:
            return await call(client, url)

    return await asyncio.gather(*[one() for _ in range(100)])


async def metrics(client: httpx.AsyncClient, server: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(server["url"] + "/metrics")
    response.raise_for_status()
    observed = response.json()
    observed["os"] = os_snapshot(observed["pid"])
    return observed


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--local-only", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))
    if len(cpus) < 3:
        raise ValueError("measurement requires three available CPUs")
    driver_cpu, fixture_cpu, gateway_cpu = cpus[-3:]
    os.sched_setaffinity(0, {driver_cpu})
    result: dict[str, Any] = {
        "qualified": False,
        "affinity": {"driver": driver_cpu, "fixture": fixture_cpu, "gateways": gateway_cpu},
        "sources": {
            str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                HERE / "run.py",
                HERE / "python_gateway.py",
                HERE / "scenario.json",
                HERE / "recorded.sse",
                HERE / "pyproject.toml",
                HERE / "uv.lock",
                HERE / "rust/src/main.rs",
                HERE / "rust/Cargo.toml",
                HERE / "rust/Cargo.lock",
            ]
        },
        "binary_sha256": hashlib.sha256(BINARY.read_bytes()).hexdigest(),
        "replay": [],
        "baseline": [],
        "live": [],
        "failures": [],
        "startup": [],
    }
    servers: list[dict[str, Any]] = []
    fixture = None
    async with httpx.AsyncClient(
        timeout=130, trust_env=False, limits=httpx.Limits(max_connections=16, keepalive_expiry=120)
    ) as client:
        try:
            fixture = await start_server(
                "fixture", "", "replay-local", {}, root, fixture_cpu, client
            )
            for concurrency in (1, 4):
                for trial in range(3):
                    started = time.monotonic()
                    observed = await batch(client, fixture["url"], concurrency)
                    wall = (time.monotonic() - started) * 1000
                    if not all(c["qualified"] for c in observed):
                        raise AssertionError("load-generator baseline failed")
                    result["baseline"].append(
                        {
                            "trial": trial,
                            "concurrency": concurrency,
                            "wall_ms": wall,
                            "calls": observed,
                        }
                    )
            dummy = {
                "Authorization": "Bearer replay-local",
                "Accept": "text/event-stream",
                "User-Agent": "decepticon-http-benchmark",
                "Accept-Encoding": "identity",
            }
            for name in NAMES:
                server = await start_server(
                    name, fixture["url"], "replay-local", dummy, root, gateway_cpu, client
                )
                servers.append(server)
                result["startup"].append(
                    {
                        "mode": "replay",
                        "name": name,
                        "startup_ms": server["startup_ms"],
                        "ready": server["ready"],
                    }
                )
                warm = await call(client, server["url"])
                if not warm["qualified"]:
                    raise AssertionError(f"{name} replay qualification failed: {warm}")
            for trial in range(3):
                order = NAMES[trial:] + NAMES[:trial]
                for name in order:
                    server = next(s for s in servers if s["name"] == name)
                    for concurrency in (1, 4):
                        before = await metrics(client, server)
                        fixture_before = await metrics(client, fixture)
                        started = time.monotonic()
                        calls = await batch(client, server["url"], concurrency)
                        wall = (time.monotonic() - started) * 1000
                        after = await metrics(client, server)
                        fixture_after = await metrics(client, fixture)
                        row = {
                            "name": name,
                            "trial": trial,
                            "concurrency": concurrency,
                            "before": before,
                            "after": after,
                            "wall_ms": wall,
                            "calls": calls,
                            "fixture_before": fixture_before,
                            "fixture_after": fixture_after,
                        }
                        result["replay"].append(row)
                        if (
                            not all(c["qualified"] for c in calls)
                            or after["attempts"] - before["attempts"] != 100
                        ):
                            raise AssertionError(f"{name} replay batch failed or retried")
                        print(
                            json.dumps(
                                {
                                    "replay": name,
                                    "trial": trial,
                                    "concurrency": concurrency,
                                    "rps": 100_000 / wall,
                                }
                            ),
                            flush=True,
                        )
            for server in servers:
                stop_server(server)
            servers.clear()
            for failure_path in ("reject", "truncated"):
                for name in NAMES:
                    server = await start_server(
                        name,
                        fixture["url"] + "/" + failure_path,
                        "replay-local",
                        dummy,
                        root,
                        gateway_cpu,
                        client,
                    )
                    servers.append(server)
                    failed = await call(client, server["url"])
                    observed = await metrics(client, server)
                    result["failures"].append(
                        {"name": name, "kind": failure_path, "call": failed, "metrics": observed}
                    )
                    if (
                        failed["qualified"]
                        or failed["completed"]
                        or observed["attempts"] != 1
                        or observed["failures"] != 1
                    ):
                        raise AssertionError("invalid response accepted or retried")
                    stop_server(server)
                    servers.clear()
            if not args.local_only:
                sys.path.insert(0, str(ROOT / "config"))
                sys.path.insert(0, str(ROOT))
                from config.codex_chatgpt_handler import _headers, get_codex_access_token

                token, account = get_codex_access_token()
                original = _headers(token, account)
                headers = {
                    "Authorization": original["Authorization"],
                    "Accept": "text/event-stream",
                    "User-Agent": original["user-agent"],
                    "Originator": original["originator"],
                    "Accept-Encoding": "identity",
                }
                if account:
                    headers["ChatGPT-Account-Id"] = account
                for name in NAMES:
                    server = await start_server(
                        name,
                        "https://chatgpt.com/backend-api/codex",
                        token,
                        headers,
                        root,
                        gateway_cpu,
                        client,
                    )
                    servers.append(server)
                    result["startup"].append(
                        {
                            "mode": "live",
                            "name": name,
                            "startup_ms": server["startup_ms"],
                            "ready": server["ready"],
                        }
                    )
                    first = await call(client, server["url"])
                    result["live"].append(
                        {
                            "name": name,
                            "phase": "first",
                            "call": first,
                            "metrics": await metrics(client, server),
                        }
                    )
                    if not first["qualified"]:
                        raise AssertionError(f"{name} first real call failed")
                for trial in range(3):
                    for name in NAMES[trial:] + NAMES[:trial]:
                        server = next(s for s in servers if s["name"] == name)
                        before = await metrics(client, server)
                        observed = await call(client, server["url"])
                        after = await metrics(client, server)
                        result["live"].append(
                            {
                                "name": name,
                                "phase": "warm",
                                "trial": trial,
                                "call": observed,
                                "before": before,
                                "after": after,
                            }
                        )
                        if not observed["qualified"] or after["attempts"] - before["attempts"] != 1:
                            raise AssertionError(f"{name} live call failed or retried")
                        print(
                            json.dumps(
                                {"live": name, "trial": trial, "wall_ms": observed["wall_ms"]}
                            ),
                            flush=True,
                        )
            result["fixture_final"] = await metrics(client, fixture)
            result["qualified"] = True
        except Exception as exc:
            result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            for server in servers:
                stop_server(server)
            if fixture:
                stop_server(fixture)
            (root / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
