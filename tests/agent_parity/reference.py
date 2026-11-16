"""Python reference for the same state-machine architecture ported in the native Rust candidate."""

import asyncio
import copy
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def resources() -> dict[str, Any]:
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    smaps = dict(
        line.split(":", 1)
        for line in Path("/proc/self/smaps_rollup").read_text().splitlines()
        if ":" in line
    )
    return {
        "cpu_ns": time.process_time_ns(),
        "peak_rss_kib": int(status["VmHWM"].split()[0]),
        "rss_kib": int(status["VmRSS"].split()[0]),
        "pss_kib": int(smaps["Pss"].split()[0]),
        "threads": int(status["Threads"]),
        "fds": len(list(Path("/proc/self/fd").iterdir())),
    }


class Framework:
    def __init__(
        self, root: Path, phase: str, profile: dict[str, Any], scenario: dict[str, Any]
    ) -> None:
        self.root, self.phase, self.profile, self.scenario = root, phase, profile, scenario
        self.db = sqlite3.connect(root / "checkpoint.sqlite")
        self.db.executescript(
            "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS checkpoints(role TEXT PRIMARY KEY,engine TEXT NOT NULL,state TEXT NOT NULL,todos TEXT NOT NULL,done TEXT); CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY AUTOINCREMENT,role TEXT NOT NULL,state TEXT NOT NULL,todos TEXT NOT NULL,done TEXT);"
        )
        self.client = httpx.AsyncClient(timeout=120)

    def event(self, kind: str, **fields: Any) -> None:
        with (self.root / "events.jsonl").open("a") as out:
            out.write(
                canonical(
                    {
                        "event": kind,
                        "phase": self.phase,
                        "pid": os.getpid(),
                        "at": time.monotonic(),
                        "engine": "python-native",
                        **fields,
                    }
                )
                + "\n"
            )

    def save(
        self, role: str, state: dict[str, Any], todos: list[dict[str, str]], done: str | None = None
    ) -> None:
        self.db.execute(
            "INSERT INTO history(role,state,todos,done) VALUES(?,?,?,?)",
            (role, canonical(state), canonical(todos), done),
        )
        self.db.execute(
            "INSERT INTO checkpoints(role,engine,state,todos,done) VALUES(?,?,?,?,?) ON CONFLICT(role) DO UPDATE SET engine=excluded.engine,state=excluded.state,todos=excluded.todos,done=excluded.done",
            (role, "python-native", canonical(state), canonical(todos), done),
        )
        self.db.commit()
        self.event("checkpoint.saved", role=role, todos=todos, completed=done is not None)

    def file_path(self, virtual_path: str, writing: bool = False) -> Path:
        if "~" in virtual_path or "\\" in virtual_path:
            raise PermissionError("invalid virtual path")
        base = self.root / ("memory" if virtual_path.startswith("/memories/") else "files")
        relative = (
            virtual_path.removeprefix("/memories/")
            if virtual_path.startswith("/memories/")
            else virtual_path.lstrip("/")
        )
        if ".." in Path(relative).parts:
            raise PermissionError("path traversal refused")
        target = base / relative
        checked = target.parent.resolve(strict=True) if writing else target.resolve(strict=True)
        if not checked.is_relative_to(base.resolve()) or (writing and target.exists()):
            raise PermissionError("file scope or overwrite refused")
        return target

    @staticmethod
    async def command(args: list[str]) -> tuple[str, str]:
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(f"docker failed: {stderr.decode()}")
        return stdout.decode(), stderr.decode()

    async def sandbox(self, role: str, command: str) -> str:
        if role != "inspector" or command != "python3 /work/analyze.py":
            raise PermissionError("sandbox command/role denied")
        name = f"agent-parity-python-native-{os.getpid()}"
        created, _ = await self.command(
            [
                "create",
                "--name",
                name,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "128m",
                "--cpus",
                "1",
                "--pids-limit",
                "32",
                "--user",
                "65534:65534",
                "--workdir",
                "/work",
                "--mount",
                f"type=bind,src={self.root / 'files'},dst=/work,readonly",
                "--entrypoint",
                "python3",
                os.environ["PARITY_SANDBOX_IMAGE"],
                "/work/analyze.py",
            ]
        )
        self.event("sandbox.created", role=role, container_id=created.strip(), command=command)
        try:
            stdout, stderr = await self.command(["start", "--attach", name])
            inspected, _ = await self.command(["inspect", name])
            container = json.loads(inspected)[0]
            keys = [
                "NetworkMode",
                "ReadonlyRootfs",
                "CapDrop",
                "SecurityOpt",
                "Memory",
                "NanoCpus",
                "PidsLimit",
            ]
            self.event(
                "sandbox.finished",
                role=role,
                container_id=container["Id"],
                image=container["Image"],
                state=container["State"],
                host_config={k: container["HostConfig"][k] for k in keys},
                user=container["Config"]["User"],
                output=stdout,
                stderr=stderr,
            )
            return stdout + "\n[Command succeeded with exit code 0]"
        finally:
            await self.command(["rm", "--force", name])
            self.event("sandbox.removed", name=name)

    async def tool(
        self, role: str, name: str, args: dict[str, Any], todos: list[dict[str, str]]
    ) -> str:
        if name not in {t["function"]["name"] for t in self.profile[role]["tools"]}:
            raise PermissionError("tool not exposed")
        self.event("tool.start", role=role, name=name, arguments=args)
        if name == "write_todos":
            if any(
                t.get("status") not in {"pending", "in_progress", "completed"}
                or not isinstance(t.get("content"), str)
                for t in args["todos"]
            ):
                raise ValueError("invalid todo")
            todos[:] = [{"content": t["content"], "status": t["status"]} for t in args["todos"]]
            output = "Updated todo list to " + repr(todos)
        elif name == "read_file":
            lines = self.file_path(args["file_path"]).read_text().splitlines()
            offset = args.get("offset", 0)
            end = min(offset + args.get("limit", 2000), len(lines))
            output = (
                f"@@ lines {offset + 1}-{end} of {len(lines)} @@\n" + "\n".join(lines[offset:end])
                if offset < len(lines)
                else ""
            )
        elif name == "write_file":
            with self.file_path(args["file_path"], True).open("xb") as out:
                out.write(args["content"].encode())
            output = "Updated file " + args["file_path"]
        elif name == "execute":
            output = await self.sandbox(role, args["command"])
        elif name == "task":
            if (
                role != "parent"
                or args["subagent_type"] != "inspector"
                or args["description"] != self.scenario["child_input"]
            ):
                raise PermissionError("invalid delegation")
            self.event(
                "subagent.start", role="inspector", parent="parent", input=args["description"]
            )
            status, output, _ = await self.run("inspector", args["description"])
            if status != "completed":
                raise AssertionError("child unexpectedly paused")
            self.event("subagent.end", role="inspector", parent="parent", output=output)
        else:
            raise ValueError("unsupported tool")
        self.event("tool.end", role=role, name=name, output=output, todos=todos)
        return output

    async def run(self, role: str, prompt: str) -> tuple[str, str, list[dict[str, str]]]:
        saved = self.db.execute(
            "SELECT state,todos,done FROM checkpoints WHERE role=? AND engine=?",
            (role, "python-native"),
        ).fetchone()
        if saved:
            state, todos, done = json.loads(saved[0]), json.loads(saved[1]), saved[2]
            if done is not None:
                return "completed", done, todos
        else:
            state = {
                "messages": [
                    {"role": "system", "content": self.profile[role]["system"]},
                    {"role": "user", "content": prompt},
                ],
                "pending": [],
                "turns": 0,
            }
            todos = []
        self.event("checkpoint.loaded", role=role, next=["continue"], todos=todos)
        self.event("agent.ready", role=role, resources=resources())
        while True:
            while state["pending"]:
                call = state["pending"][0]
                name, args = call["function"]["name"], json.loads(call["function"]["arguments"])
                if (
                    role == "parent"
                    and self.phase == "prepare"
                    and name == "read_file"
                    and args["file_path"] == "/memories/result.json"
                ):
                    self.save(role, state, todos)
                    self.event("process.paused", todos=todos, resources=resources())
                    return "paused", "", todos
                output = await self.tool(role, name, args, todos)
                state["messages"].append(
                    {"role": "tool", "tool_call_id": call["id"], "content": output}
                )
                state["pending"].pop(0)
                self.save(role, state, todos)
            if state["turns"] >= 20:
                raise RuntimeError("model turn budget")
            state["turns"] += 1
            self.event("model.start", role=role, turn=state["turns"])
            response = await self.client.post(
                os.environ["OPENAI_BASE_URL"] + "/chat/completions",
                json={
                    "model": self.scenario["model"],
                    "messages": state["messages"],
                    "tools": self.profile[role]["tools"],
                    "stream": False,
                },
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            state["messages"].append(message)
            state["pending"] = copy.deepcopy(message.get("tool_calls", []))
            self.event("model.end", role=role, turn=state["turns"])
            if not state["pending"]:
                output = message["content"]
                self.save(role, state, todos, output)
                self.event("process.completed", role=role, todos=todos, resources=resources())
                return "completed", output, todos
            self.save(role, state, todos)


async def main() -> None:
    imported = resources()
    root, phase = Path(sys.argv[1]).resolve(), sys.argv[2]
    profile, scenario = (
        json.loads(Path(sys.argv[3]).read_text()),
        json.loads(Path(sys.argv[4]).read_text()),
    )
    framework = Framework(root, phase, profile, scenario)
    framework.event(
        "process.start",
        framework="python-native",
        resources=imported,
        sqlite_version=sqlite3.sqlite_version,
    )
    if (root / "memory/AGENTS.md").read_text() != scenario["memory_instructions"]:
        raise ValueError("memory instructions differ from profile")
    try:
        status, _, todos = await framework.run("parent", scenario["input_prompt"])
        print(json.dumps({"status": status, "todos": todos}), flush=True)
        if status == "paused":
            sys.stdin.readline()
    finally:
        await framework.client.aclose()
        framework.db.close()


if __name__ == "__main__":
    asyncio.run(main())
