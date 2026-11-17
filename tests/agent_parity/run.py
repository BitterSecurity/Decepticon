"""Run a live-model functional qualification and retain its execution evidence."""

import argparse
import hashlib
import json
import os
import resource
import selectors
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "config"))
from config import codex_chatgpt_handler as provider_module  # noqa: E402
from config.codex_chatgpt_handler import CodexChatGPTCustomHandler, _request_body  # noqa: E402

CALLS: list[dict[str, Any]] = []
PROVIDER = CodexChatGPTCustomHandler()
CURRENT_PHASE = "prepare"
REPLAY: dict[str, list[dict[str, Any]]] = {}
CURSORS: dict[str, int] = {}
REPLAY_MODE = False


def canonical_request(
    model: str, messages: list[dict[str, Any]], options: dict[str, Any] | None
) -> dict[str, Any]:
    body = _request_body(model, messages, options)
    for entry in body["input"]:
        if entry.get("type") == "function_call":
            entry["arguments"] = json.dumps(
                json.loads(entry["arguments"]),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
    return body


provider_module._request_body = canonical_request


class Relay(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        started = time.monotonic()
        row: dict[str, Any] = {"phase": CURRENT_PHASE, "started": started}
        try:
            if self.path != "/v1/chat/completions" or len(CALLS) >= 40:
                raise ValueError("model request budget or endpoint")
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            row["request"] = request
            row["upstream_request"] = canonical_request(
                request["model"],
                request["messages"],
                {"tools": request.get("tools"), "reasoning_effort": "low"},
            )
            role = (
                "inspector"
                if "You are inspector." in row["upstream_request"]["instructions"]
                else "parent"
            )
            row["role"] = role
            if REPLAY_MODE:
                key = CURRENT_PHASE + ":" + role
                index = CURSORS.get(key, 0)
                expected = REPLAY[key][index]
                if row["upstream_request"] != expected["upstream_request"]:
                    raise AssertionError("replay request differs from captured live trajectory")
                payload = expected["response"]
                CURSORS[key] = index + 1
                row["replay"] = True
            else:
                response = PROVIDER.completion(
                    model=request["model"],
                    messages=request["messages"],
                    optional_params={"tools": request.get("tools"), "reasoning_effort": "low"},
                    timeout=120,
                )
                payload = response.model_dump(exclude_none=True)
            # Retain public assistant/tool messages, not hidden reasoning or authentication headers.
            row["response"] = {
                "id": payload.get("id"),
                "object": payload.get("object", "chat.completion"),
                "created": payload.get("created"),
                "model": payload.get("model"),
                "usage": payload.get("usage"),
                "choices": [
                    {
                        "index": c.get("index", 0),
                        "finish_reason": c.get("finish_reason"),
                        "message": {
                            k: v
                            for k, v in c["message"].items()
                            if k in {"role", "content", "tool_calls"}
                        },
                    }
                    for c in payload["choices"]
                ],
            }
            encoded = json.dumps(payload).encode()
            status = 200
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            encoded = json.dumps({"error": {"message": type(exc).__name__}}).encode()
            status = 502
        row["elapsed_ms"] = (time.monotonic() - started) * 1000
        CALLS.append(row)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def observed_process(
    command: list[str], env: dict[str, str], work: Path, phase: str
) -> dict[str, Any]:
    """Observe a fresh child and kill it at its acknowledged durable pause."""
    peaks = {"rss_kib": 0, "threads": 0, "fds": 0}
    final_counters: dict[str, int] = {}
    sampling_end: list[str] = []
    stopped = threading.Event()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    with (work / f"{phase}.stderr").open("w") as stderr:
        child = subprocess.Popen(
            command,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
        )

        def sample() -> None:
            proc = Path(f"/proc/{child.pid}")
            while not stopped.is_set():
                try:
                    status = dict(
                        line.split(":", 1) for line in (proc / "status").read_text().splitlines()
                    )
                    for metric, key in (("rss_kib", "VmRSS"), ("threads", "Threads")):
                        if key in status:
                            peaks[metric] = max(peaks[metric], int(status[key].split()[0]))
                    peaks["fds"] = max(peaks["fds"], len(list((proc / "fd").iterdir())))
                    for key in ("voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"):
                        final_counters[key] = int(status[key])
                    for line in (proc / "io").read_text().splitlines():
                        key, value = line.split(":")
                        final_counters[key] = int(value)
                except (FileNotFoundError, ProcessLookupError, PermissionError) as exc:
                    sampling_end.append(type(exc).__name__)
                    return
                stopped.wait(0.01)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        prefix = ""
        assert child.stdout is not None
        try:
            if phase == "prepare":
                with selectors.DefaultSelector() as selector:
                    selector.register(child.stdout, selectors.EVENT_READ)
                    if not selector.select(300):
                        raise TimeoutError("prepare did not acknowledge pause")
                    prefix = child.stdout.readline()
                if not prefix or json.loads(prefix).get("status") != "paused":
                    raise AssertionError("prepare failed before durable pause")
                child.kill()
            stdout, _ = child.communicate(timeout=300)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            stopped.set()
            sampler.join()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    expected = -signal.SIGKILL if phase == "prepare" else 0
    row = {
        "phase": phase,
        "exit": child.returncode,
        "expected_exit": expected,
        "wall_ms": (time.monotonic() - started) * 1000,
        "stdout": prefix + stdout,
        "local_process_tree_user_cpu_ms": (after.ru_utime - before.ru_utime) * 1000,
        "local_process_tree_system_cpu_ms": (after.ru_stime - before.ru_stime) * 1000,
        "sampled_peaks_10ms": peaks,
        "last_sample_counters": final_counters,
        "sampling_end": sampling_end,
    }
    if child.returncode != expected:
        raise RuntimeError(
            f"{phase} process exited {child.returncode}; see {work / (phase + '.stderr')}"
        )
    return row


def main() -> None:
    global CURRENT_PHASE, REPLAY_MODE
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default="bb01513e07b0")
    parser.add_argument(
        "--framework",
        choices=["deepagents", "langchain", "python-native", "native", "rig"],
        default="deepagents",
    )
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    REPLAY_MODE = args.replay is not None
    REPLAY.clear()
    CURSORS.clear()
    if args.replay:
        trace = json.loads(args.replay.read_text())
        if (
            not isinstance(trace, dict)
            or trace.get("qualified") is not True
            or trace.get("mode") != "live"
            or not isinstance(trace.get("model_calls"), list)
            or not trace["model_calls"]
        ):
            raise ValueError("replay requires a qualified nonempty live trace")
        for call in trace["model_calls"]:
            role = (
                "inspector"
                if "You are inspector." in call["upstream_request"]["instructions"]
                else "parent"
            )
            REPLAY.setdefault(call["phase"] + ":" + role, []).append(call)
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=False)
    (work / "files").mkdir(mode=0o755)
    (work / "memory").mkdir()
    scenario = json.loads((HERE / "scenario.json").read_text())
    (work / "files/input.json").write_text(json.dumps(scenario["records"]) + "\n")
    (work / "files/analyze.py").write_text(scenario["sandbox_program"])
    (work / "memory/AGENTS.md").write_text(scenario["memory_instructions"])
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", args.image], text=True
    ).strip()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ("PATH", "HOME", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT")
    }
    env.update(
        OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1",
        OPENAI_API_KEY="local-parity",
        PARITY_SANDBOX_IMAGE=image,
        LANGSMITH_TRACING="false",
        PYTHONHASHSEED="0",
    )
    evidence: dict[str, Any] = {
        "qualified": False,
        "framework": args.framework,
        "mode": "replay" if args.replay else "live",
        "clock_anchor": {"monotonic": time.monotonic(), "unix": time.time()},
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest()
        if args.binary
        else None,
        "scenario": scenario,
        "image": image,
        "phases": [],
        "source_sha256": {
            str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                HERE / "case.py",
                HERE / "run.py",
                HERE / "scenario.json",
                HERE / "pyproject.toml",
                HERE / "uv.lock",
                HERE / "layers.py",
                HERE / "reference.py",
                HERE / "profile.json",
                HERE / "rust/src/main.rs",
                HERE / "rust/Cargo.toml",
                HERE / "rust/Cargo.lock",
            ]
        },
    }
    try:
        for phase in ("prepare", "resume"):
            CURRENT_PHASE = phase
            command = (
                [
                    str(args.binary.resolve()),
                    str(work),
                    phase,
                    str(HERE / "profile.json"),
                    str(HERE / "scenario.json"),
                ]
                if args.framework in {"native", "rig"} and args.binary
                else [sys.executable, str(HERE / "case.py"), str(work), phase, args.framework]
            )
            if args.framework in {"native", "rig"} and not args.binary:
                raise ValueError("Rust candidate requires --binary")
            if args.framework == "python-native":
                command = [
                    sys.executable,
                    str(HERE / "reference.py"),
                    str(work),
                    phase,
                    str(HERE / "profile.json"),
                    str(HERE / "scenario.json"),
                ]
            row = observed_process(command, env, work, phase)
            evidence["phases"].append(row)
            print(json.dumps(row), flush=True)
            status = json.loads(row["stdout"])
            if status["status"] != ("paused" if phase == "prepare" else "completed"):
                raise AssertionError("incorrect phase status")
            if phase == "prepare":
                evidence["memory_before_restart"] = json.loads(
                    (work / "memory/result.json").read_text()
                )
                if (work / "files/report.json").exists():
                    raise AssertionError("report exists before memory read/resume")
        events = [json.loads(line) for line in (work / "events.jsonl").read_text().splitlines()]
        starts = [e for e in events if e["event"] == "tool.start"]

        def count(name: str, phase: str | None = None) -> int:
            return sum(e["name"] == name and (phase is None or e["phase"] == phase) for e in starts)

        report = json.loads((work / "files/report.json").read_text())
        delegation = next(e for e in starts if e["name"] == "task")
        if delegation["arguments"]["description"] != scenario["child_input"]:
            raise AssertionError("child input prompt differs from the common scenario")
        memory = json.loads((work / "memory/result.json").read_text())
        if (
            report != scenario["expected"]
            or memory != report
            or evidence["memory_before_restart"] != report
        ):
            raise AssertionError("report/memory differs from the independently specified oracle")
        if (
            count("task") != 1
            or count("execute") != 1
            or count("task", "resume")
            or count("execute", "resume")
        ):
            raise AssertionError("delegation/sandbox duplicated or missing")
        pids = {e["pid"] for e in events if e["event"] == "process.start"}
        if len(pids) != 2:
            raise AssertionError("did not restart in a new process")
        completed = next(
            e
            for e in events
            if e["event"] == "process.completed" and e.get("role", "parent") == "parent"
        )
        if len(completed["todos"]) != 3 or any(
            t["status"] != "completed" for t in completed["todos"]
        ):
            raise AssertionError("plan not completed")
        sandbox = next(e for e in events if e["event"] == "sandbox.finished")
        if (
            sandbox["host_config"]["NetworkMode"] != "none"
            or not sandbox["host_config"]["ReadonlyRootfs"]
            or sandbox["state"]["ExitCode"]
        ):
            raise AssertionError("sandbox isolation or execution failed")
        loaded = next(
            e for e in events if e["event"] == "checkpoint.loaded" and e["phase"] == "resume"
        )
        paused = next(e for e in events if e["event"] == "process.paused")
        initial_plan = next(e for e in starts if e["name"] == "write_todos")
        if starts.index(initial_plan) >= starts.index(delegation):
            raise AssertionError("delegation preceded the initial plan")
        if loaded["todos"] != paused["todos"]:
            raise AssertionError("restored plan differs from durable pause")
        if not loaded["next"] or len(loaded["todos"]) != 3:
            raise AssertionError("checkpoint did not restore pending work and plan")
        if not any(
            e["event"] == "tool.start"
            and e["name"] == "read_file"
            and e["phase"] == "resume"
            and "/memories/result.json" in str(e["arguments"])
            for e in events
        ):
            raise AssertionError("persisted memory not actually read after restart")
        for phase in ("prepare", "resume"):
            stage = [e for e in events if e["phase"] == phase]
            imported = next(e["resources"] for e in stage if e["event"] == "process.start")
            ready = next(
                e["resources"]
                for e in stage
                if e["event"] == "agent.ready" and e.get("role", "parent") == "parent"
            )
            ended = next(
                e["resources"]
                for e in stage
                if e["event"] in {"process.paused", "process.completed"}
                and e.get("role", "parent") == "parent"
            )
            evidence["phases"][0 if phase == "prepare" else 1].update(
                import_cpu_ms=imported["cpu_ns"] / 1e6,
                setup_cpu_ms=(ready["cpu_ns"] - imported["cpu_ns"]) / 1e6,
                active_cpu_ms=(ended["cpu_ns"] - ready["cpu_ns"]) / 1e6,
                resources=ended,
            )
        if REPLAY_MODE and any(CURSORS.get(key, 0) != len(value) for key, value in REPLAY.items()):
            raise AssertionError("replay did not consume every captured response")
        with sqlite3.connect(work / "checkpoint.sqlite") as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise AssertionError("checkpoint integrity")
            evidence["checkpoint_bytes"] = (work / "checkpoint.sqlite").stat().st_size
            evidence["checkpoint_rows"] = connection.execute(
                "SELECT COUNT(*) FROM checkpoints"
            ).fetchone()[0]
        evidence.update(
            qualified=True,
            report=report,
            tool_counts={n: count(n) for n in {e["name"] for e in starts}},
            restarted_pids=sorted(pids),
        )
    except Exception as exc:
        evidence["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        server.shutdown()
        server.server_close()
        evidence["model_calls"] = CALLS
        evidence["events"] = (
            [json.loads(line) for line in (work / "events.jsonl").read_text().splitlines()]
            if (work / "events.jsonl").exists()
            else []
        )
        args.output.write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
