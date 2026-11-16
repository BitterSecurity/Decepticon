"""Ramp concurrent paced HTTP calls inside identical kernel-enforced budgets."""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import httpx

import run

POINTS = [(1, 8), (4, 16), (16, 32), (64, 128), (128, 256), (256, 512), (512, 1024)]


async def load_call(client: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    started = time.monotonic()
    text = ""
    done = False
    error = None
    model = None
    first = None
    async with client.post(url + "/invoke", data=b"") as response:
        response.raise_for_status()
        async for line in response.content:
            if not line.startswith(b"data: "):
                continue
            payload = json.loads(line[6:])
            if payload.get("delta"):
                if first is None:
                    first = (time.monotonic() - started) * 1000
                text += payload["delta"]
            if payload.get("done"):
                done = True
                model = payload.get("model")
                if payload["text"] != text:
                    raise AssertionError("stream and final text differ")
            if payload.get("error"):
                error = payload["error"]
    return {
        "qualified": done and error is None and text == run.EXPECTED and model == "gpt-6.1-sol",
        "wall_ms": (time.monotonic() - started) * 1000,
        "ttft_ms": first,
        "text": text,
        "model": model,
        "completed": done,
        "error": error,
    }


def group_snapshot(group: Path) -> dict[str, Any]:
    try:
        result: dict[str, Any] = {}
        for name in ("cpu.max", "memory.max", "memory.swap.max", "memory.current", "memory.peak"):
            result[name] = (group / name).read_text().strip()
        for name in ("cpu.stat", "memory.events", "memory.stat"):
            result[name] = dict(
                (k, int(v))
                for k, v in (line.split() for line in (group / name).read_text().splitlines())
            )
        return result
    except FileNotFoundError:
        return {"removed": True}


def unit_state(unit: str) -> dict[str, str]:
    result = subprocess.run(
        [
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=Result",
            "--property=ActiveState",
            "--property=MemoryPeak",
            "--property=CPUUsageNSec",
            "--property=ExecMainStatus",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


async def start(
    name: str, base: str, root: Path, cpu: int, trial: int, client: httpx.AsyncClient
) -> dict[str, Any]:
    port = run.free_port()
    unit = f"llm-http-scale-{os.getpid()}-{trial}-{name}.service"
    command = (
        [sys.executable, str(run.HERE / "python_gateway.py"), name, str(port)]
        if name != "native"
        else [str(run.BINARY), name, str(port), str(run.HERE)]
    )
    settings = {
        "BENCH_BASE": base + "/paced",
        "BENCH_TOKEN": "replay-local",
        "BENCH_HEADERS": json.dumps(
            {
                "Authorization": "Bearer replay-local",
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
            }
        ),
        "BENCH_POOL_LIMIT": "1024",
        "PYTHONHASHSEED": "0",
        "LANGSMITH_TRACING": "false",
    }
    launch = [
        "systemd-run",
        "--user",
        "--quiet",
        "--wait",
        "--pipe",
        "--unit=" + unit,
        "--property=CPUQuota=100%",
        "--property=MemoryMax=256M",
        "--property=MemorySwapMax=0",
        "--property=LimitNOFILE=8192",
        "--property=MemoryAccounting=yes",
        "--property=CPUAccounting=yes",
    ]
    launch += [f"--setenv={key}={value}" for key, value in settings.items()]
    with (root / (unit + ".log")).open("w") as log:
        process = subprocess.Popen(
            [*launch, "taskset", "-c", str(cpu), *command], stdout=log, stderr=log
        )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(1200):
            if process.poll() is not None:
                raise RuntimeError(f"budgeted {name} failed startup: {unit_state(unit)}")
            try:
                response = await client.get(url + "/metrics")
                response.raise_for_status()
                observed = response.json()
                path = Path("/sys/fs/cgroup") / Path(
                    f"/proc/{observed['pid']}/cgroup"
                ).read_text().split("::", 1)[1].strip().lstrip("/")
                budget = group_snapshot(path)
                if (
                    budget["cpu.max"] != "100000 100000"
                    or budget["memory.max"] != "268435456"
                    or budget["memory.swap.max"] != "0"
                ):
                    raise AssertionError("kernel resource budgets differ")
                limit_line = next(
                    line
                    for line in Path(f"/proc/{observed['pid']}/limits").read_text().splitlines()
                    if line.startswith("Max open files")
                )
                if limit_line.split()[3:5] != ["8192", "8192"]:
                    raise AssertionError("FD limits differ")
                snapshot = run.os_snapshot(observed["pid"])
                if snapshot["affinity"] != str(cpu):
                    raise AssertionError("CPU affinity differs")
                observed["os"] = snapshot
                observed["fd_limits"] = [8192, 8192]
                return {
                    "name": name,
                    "url": url,
                    "unit": unit,
                    "process": process,
                    "group": path,
                    "ready": observed,
                    "initial_budget": budget,
                }
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
        raise TimeoutError(f"budgeted {name} not ready")
    except BaseException:
        stop({"unit": unit, "process": process})
        raise


def stop(server: dict[str, Any]) -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", server["unit"]],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    try:
        server["process"].wait(timeout=5)
    except subprocess.TimeoutExpired:
        server["process"].kill()
        server["process"].wait()
    subprocess.run(
        ["systemctl", "--user", "reset-failed", server["unit"]],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))
    driver_cpu, fixture_cpu, gateway_cpu = cpus[-3:]
    os.sched_setaffinity(0, {driver_cpu})
    result: dict[str, Any] = {
        "complete": False,
        "rows": [],
        "startup": [],
        "driver_baseline": [],
        "driver": {"library": "aiohttp", "version": aiohttp.__version__, "pool_limit": 4096},
        "budget": {"cpu.max": "100000 100000", "memory.max": 268435456, "memory.swap.max": 0},
        "affinity": {"driver": driver_cpu, "fixture": fixture_cpu, "gateways": gateway_cpu},
        "slo": {"success_rate": 0.99, "p95_wall_ms": 3000},
        "pace": {"first_text_wait_ms": 1750, "between_delta_wait_ms": 5, "synthetic_timing": True},
        "binary_sha256": hashlib.sha256(run.BINARY.read_bytes()).hexdigest(),
        "sources": {
            p: hashlib.sha256((run.HERE / p).read_bytes()).hexdigest()
            for p in [
                "python_gateway.py",
                "rust/src/main.rs",
                "scenario.json",
                "recorded.sse",
                "scale.py",
            ]
        },
    }
    fixture = None
    servers = []
    async with (
        aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=4096, keepalive_timeout=120),
            timeout=aiohttp.ClientTimeout(total=130),
            trust_env=False,
        ) as load_client,
        httpx.AsyncClient(
            timeout=130,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=4096, max_keepalive_connections=4096, keepalive_expiry=120
            ),
        ) as client,
    ):
        try:
            fixture = await run.start_server(
                "fixture", "", "replay-local", {}, root, fixture_cpu, client
            )
            for concurrency, count in POINTS:
                semaphore = asyncio.Semaphore(concurrency)

                async def baseline_one() -> dict[str, Any]:
                    async with semaphore:
                        return await load_call(load_client, fixture["url"])

                started = time.monotonic()
                cpu_started = time.process_time_ns()
                calls = await asyncio.gather(*[baseline_one() for _ in range(count)])
                result["driver_baseline"].append(
                    {
                        "concurrency": concurrency,
                        "count": count,
                        "wall_ms": (time.monotonic() - started) * 1000,
                        "driver_cpu_ms": (time.process_time_ns() - cpu_started) / 1e6,
                        "calls": calls,
                    }
                )
                if not all(c["qualified"] for c in calls):
                    raise AssertionError("driver baseline failed")
            for trial in range(3):
                for name in run.NAMES:
                    server = await start(name, fixture["url"], root, gateway_cpu, trial, client)
                    servers.append(server)
                    warm = await run.call(client, server["url"])
                    if not warm["qualified"]:
                        raise AssertionError("paced qualification failed")
                    result["startup"].append(
                        {
                            "trial": trial,
                            "name": name,
                            "ready": server["ready"],
                            "initial_budget": server["initial_budget"],
                        }
                    )
                for concurrency, count in POINTS:
                    for name in run.NAMES[trial:] + run.NAMES[:trial]:
                        server = next(s for s in servers if s["name"] == name)
                        if server["process"].poll() is not None:
                            result["rows"].append(
                                {
                                    "trial": trial,
                                    "name": name,
                                    "concurrency": concurrency,
                                    "count": count,
                                    "unavailable": True,
                                    "unit_state": unit_state(server["unit"]),
                                }
                            )
                            continue
                        before = group_snapshot(server["group"])
                        before_os = run.os_snapshot(server["ready"]["pid"])
                        fixture_before = await run.metrics(client, fixture)
                        semaphore = asyncio.Semaphore(concurrency)
                        samples = []
                        sampling = True

                        async def sample() -> None:
                            while sampling:
                                samples.append(group_snapshot(server["group"]))
                                await asyncio.sleep(0.1)

                        sampler = asyncio.create_task(sample())

                        async def one() -> dict[str, Any]:
                            async with semaphore:
                                started = time.monotonic()
                                try:
                                    return await load_call(load_client, server["url"])
                                except Exception as exc:
                                    return {
                                        "qualified": False,
                                        "error": type(exc).__name__,
                                        "wall_ms": (time.monotonic() - started) * 1000,
                                    }

                        started = time.monotonic()
                        cpu_started = time.process_time_ns()
                        try:
                            calls = await asyncio.gather(*[one() for _ in range(count)])
                        finally:
                            ended = time.monotonic()
                            cpu_ended = time.process_time_ns()
                            sampling = False
                            await sampler
                        wall_ms = (ended - started) * 1000
                        alive = server["process"].poll() is None
                        row = {
                            "trial": trial,
                            "name": name,
                            "concurrency": concurrency,
                            "count": count,
                            "wall_ms": wall_ms,
                            "driver_cpu_ms": (cpu_ended - cpu_started) / 1e6,
                            "fixture_before": fixture_before,
                            "fixture_after": await run.metrics(client, fixture),
                            "calls": calls,
                            "before": before,
                            "after": group_snapshot(server["group"]),
                            "samples": samples,
                            "before_os": before_os,
                            "unit_state": unit_state(server["unit"]),
                        }
                        if alive:
                            row["after_os"] = run.os_snapshot(server["ready"]["pid"])
                        result["rows"].append(row)
                        (root / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")
                        print(
                            json.dumps(
                                {
                                    "trial": trial,
                                    "name": name,
                                    "concurrency": concurrency,
                                    "successes": sum(c["qualified"] for c in calls),
                                    "count": count,
                                    "rps": count * 1000 / wall_ms,
                                    "alive": alive,
                                }
                            ),
                            flush=True,
                        )
                for server in servers:
                    stop(server)
                servers.clear()
            result["complete"] = True
        finally:
            for server in servers:
                stop(server)
            if fixture:
                run.stop_server(fixture)
            (root / "evidence.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
