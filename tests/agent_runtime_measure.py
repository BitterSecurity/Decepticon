"""Linux process measurements with a shared, unmeasured real-LLM relay."""

import argparse
import hashlib
import json
import os
import platform
import resource
import select
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "benchmark/agent-runtime"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "config"))
from config.codex_chatgpt_handler import CodexChatGPTCustomHandler, _message_text  # noqa: E402

REQUESTS: list[dict[str, Any]] = []
HANDLER = CodexChatGPTCustomHandler()


class Relay(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        start = time.perf_counter()
        record: dict[str, Any] = {"status": "error"}
        try:
            if self.path != "/v1/chat/completions" or len(REQUESTS) >= 40:
                raise ValueError("endpoint or request budget")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            names = [t["function"]["name"] for t in body.get("tools", [])]
            if names != ["echo"] or body.get("stream"):
                raise ValueError("all cases must expose only echo and disable streaming")
            system = "\n\n".join(
                _message_text(m.get("content")) for m in body["messages"] if m["role"] == "system"
            )
            record.update(
                tool_names=names,
                request_bytes=len(json.dumps(body).encode()),
                tool_definitions=body["tools"],
                system_chars=len(system),
                system_sha256=hashlib.sha256(system.encode()).hexdigest(),
                request_sha256=hashlib.sha256(
                    json.dumps(body, sort_keys=True).encode()
                ).hexdigest(),
            )
            response = HANDLER.completion(
                model=body["model"],
                messages=body["messages"],
                optional_params={"tools": body["tools"], "reasoning_effort": "low"},
                timeout=120,
            )
            payload = response.model_dump(exclude_none=True)
            record.update(status="ok", model=payload.get("model"), usage=payload.get("usage", {}))
            data = json.dumps(payload).encode()
            status = 200
        except Exception as exc:
            # Provider errors may contain credentials or response bodies; never persist them.
            record["error_type"] = type(exc).__name__
            data = json.dumps(
                {"error": {"message": record["error_type"], "type": "benchmark_relay"}}
            ).encode()
            status = 502
        record["elapsed_ms"] = (time.perf_counter() - start) * 1000
        REQUESTS.append(record)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def snapshot(pid: int) -> dict[str, Any]:
    root = Path(f"/proc/{pid}")
    status = dict(
        line.split(":", 1) for line in (root / "status").read_text().splitlines() if ":" in line
    )
    smaps = dict(
        line.split(":", 1)
        for line in (root / "smaps_rollup").read_text().splitlines()
        if ":" in line
    )
    io = dict(line.split(":", 1) for line in (root / "io").read_text().splitlines())
    return {
        "rss_kib": int(status["VmRSS"].split()[0]),
        "hwm_kib": int(status["VmHWM"].split()[0]),
        "pss_kib": int(smaps["Pss"].split()[0]),
        "threads": int(status["Threads"]),
        "fds": len(list((root / "fd").iterdir())),
        "io": {k: int(v) for k, v in io.items()},
    }


def line_until(
    process: subprocess.Popen[str], deadline: float, peaks: dict[str, int]
) -> dict[str, Any]:
    assert process.stdout is not None  # All measured children use stdout=PIPE.
    while time.perf_counter() < deadline:
        try:
            s = snapshot(process.pid)
            for key in ("threads", "fds", "rss_kib"):
                peaks[key] = max(peaks.get(key, 0), s[key])
        except (FileNotFoundError, ProcessLookupError, KeyError):
            # The child may exit during /proc sampling; still read its pipe so
            # protocol errors or EOF remain fatal below, with the deadline intact.
            pass
        if select.select([process.stdout], [], [], 0.01)[0]:
            line = process.stdout.readline()
            if not line:
                raise RuntimeError("case exited before completing protocol")
            return json.loads(line)
    raise TimeoutError("case exceeded wall-time budget")


def measure(name: str, command: list[str], config: dict[str, Any], trial: int) -> dict[str, Any]:
    # Credentials remain in the relay. Children receive only loopback access.
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR")}
    env.update(
        OPENAI_API_KEY="local-benchmark",
        OPENAI_BASE_URL=config["base_url"],
        LANGSMITH_TRACING="false",
        PYTHONHASHSEED="0",
        OMP_NUM_THREADS="1",
    )
    peaks = {}
    first = len(REQUESTS)
    with tempfile.TemporaryFile(mode="w+") as err:
        start = time.perf_counter()
        p = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            env=env,
            cwd=HERE,
        )
        assert p.stdin is not None and p.stdout is not None  # Both pipes are requested above.
        try:
            if line_until(p, start + 180, peaks) != {"stage": "ready"}:
                raise RuntimeError("invalid ready protocol")
            startup_ms = (time.perf_counter() - start) * 1000
            ready = snapshot(p.pid)
            work_start = time.perf_counter()
            p.stdin.write(json.dumps(config) + "\n")
            p.stdin.flush()
            result = line_until(p, work_start + 300, peaks)
            work_ms = (time.perf_counter() - work_start) * 1000
            done = snapshot(p.pid)
            p.stdin.write("exit\n")
            p.stdin.flush()
            _, status, usage = os.wait4(p.pid, 0)
            p.returncode = os.waitstatus_to_exitcode(status)
            if p.returncode:
                raise RuntimeError(f"case exit {p.returncode}")
        except BaseException:
            p.kill()
            p.wait()
            err.seek(0)
            print(err.read()[-4000:], file=sys.stderr)
            raise
        finally:
            p.stdin.close()
            p.stdout.close()
    requests = REQUESTS[first:]
    if result != {"output": "DONE:" + config["marker"], "tool_calls": 1}:
        raise AssertionError(f"incorrect case result: {result}")
    if len(requests) != 2 or any(r["status"] != "ok" for r in requests):
        raise AssertionError("expected two successful real LLM requests")
    expected_tools = [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Return the supplied text.",
                "parameters": config["schema"],
            },
        }
    ]
    if any(
        r["system_sha256"] != hashlib.sha256(config["system"].encode()).hexdigest()
        or r["tool_definitions"] != expected_tools
        for r in requests
    ):
        raise AssertionError("framework changed the normalized system prompt or tool schema")
    return {
        "case": name,
        "trial": trial,
        "startup_ms": startup_ms,
        "work_ms": work_ms,
        "relay_ms": sum(r["elapsed_ms"] for r in requests),
        "ready": ready,
        "done": done,
        "sampled_peaks": peaks,
        "peak_rss_kib": done["hwm_kib"],
        "wait4_maxrss_kib": usage.ru_maxrss,
        "user_cpu_ms": usage.ru_utime * 1000,
        "system_cpu_ms": usage.ru_stime * 1000,
        "voluntary_switches": usage.ru_nvcsw,
        "involuntary_switches": usage.ru_nivcsw,
        "minor_faults": usage.ru_minflt,
        "major_faults": usage.ru_majflt,
        "input_blocks": usage.ru_inblock,
        "output_blocks": usage.ru_oublock,
        "result": result,
        "requests": requests,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bin-dir", type=Path, default=HERE / "target")
    args = parser.parse_args()
    if not 1 <= args.trials <= 5:
        parser.error("trials must be 1..5 (bounded paid/subscription calls)")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = {
        "model": args.model,
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "system": "Call echo exactly once with the supplied marker. Then reply exactly DONE:<marker>, using the tool result. No other text.",
        "schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
    cases = [
        ("native", [str(args.bin_dir.resolve() / "native-case")]),
        ("rig", [str(args.bin_dir.resolve() / "rig-case")]),
        ("deepagents", [sys.executable, str(HERE / "deepagents_case.py")]),
    ]
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "model": args.model,
        "reasoning_effort": "low",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu": next(
            (
                s.split(":", 1)[1].strip()
                for s in Path("/proc/cpuinfo").read_text().splitlines()
                if s.startswith("model name")
            ),
            "unknown",
        ),
        "logical_cpus": os.cpu_count(),
        "load_average_at_start": os.getloadavg(),
        "config": {k: v for k, v in config.items() if k != "base_url"},
        "samples": [],
        "harness_ready": snapshot(os.getpid()),
        "rust": subprocess.check_output(["rustc", "+1.97.1", "--version"], text=True).strip(),
        "base_commit": subprocess.check_output(
            ["git", "rev-parse", "origin/main"], cwd=ROOT, text=True
        ).strip(),
        "source_hashes": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__).resolve(),
                HERE / "deepagents_case.py",
                HERE / "src/main.rs",
                HERE / "Cargo.toml",
                HERE / "pyproject.toml",
                HERE / "uv.lock",
                HERE / "Cargo.lock",
            ]
        },
        "binary_bytes": {
            name: (args.bin_dir / f"{name}-case").stat().st_size for name in ("native", "rig")
        },
        "binary_sha256": {
            name: hashlib.sha256((args.bin_dir / f"{name}-case").read_bytes()).hexdigest()
            for name in ("native", "rig")
        },
    }
    harness_cpu = resource.getrusage(resource.RUSAGE_SELF)
    try:
        for trial in range(args.trials):
            # A balanced Latin rotation: each framework occupies every position once in 3 trials.
            for name, command in cases[trial % 3 :] + cases[: trial % 3]:
                marker = f"marker-{trial}"
                result = measure(
                    name,
                    command,
                    {**config, "marker": marker, "prompt": f"Marker: {marker}"},
                    trial,
                )
                report["samples"].append(result)
                print(
                    name,
                    trial,
                    "rss KiB",
                    result["peak_rss_kib"],
                    "work ms",
                    round(result["work_ms"]),
                    flush=True,
                )
    finally:
        server.shutdown()
        server.server_close()
        report["requests"] = REQUESTS
        report["completed"] = len(report["samples"]) == args.trials * 3
        report["harness_done"] = snapshot(os.getpid())
        after = resource.getrusage(resource.RUSAGE_SELF)
        report["harness_cpu_ms"] = {
            "user": (after.ru_utime - harness_cpu.ru_utime) * 1000,
            "system": (after.ru_stime - harness_cpu.ru_stime) * 1000,
        }
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    for name, _ in cases:
        samples = [s for s in report["samples"] if s["case"] == name]
        print(
            name,
            {
                k: round(statistics.median(s[k] for s in samples), 2)
                for k in (
                    "peak_rss_kib",
                    "startup_ms",
                    "work_ms",
                    "user_cpu_ms",
                    "system_cpu_ms",
                    "relay_ms",
                )
            },
        )


if __name__ == "__main__":
    main()
