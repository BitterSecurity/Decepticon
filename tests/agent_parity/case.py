"""Qualify built-in Deep Agents features with observable tool and graph execution."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    create_deep_agent,
    register_harness_profile,
)
from deepagents.backends import CompositeBackend, FilesystemBackend
from deepagents.backends.protocol import ExecuteResponse, SandboxBackendProtocol
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import SecretStr

from layers import compose

WORK = Path(sys.argv[1]).resolve()
PHASE = sys.argv[2]
FRAMEWORK = sys.argv[3] if len(sys.argv) > 3 else "deepagents"
SCENARIO = json.loads((Path(__file__).parent / "scenario.json").read_text())
LOCK = threading.Lock()


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


def event(kind: str, **fields: Any) -> None:
    row = {"event": kind, "phase": PHASE, "pid": os.getpid(), "at": time.monotonic(), **fields}
    with LOCK, (WORK / "events.jsonl").open("a") as out:
        out.write(json.dumps(row, default=str) + "\n")


def message_view(message: Any) -> dict[str, Any]:
    return {
        "type": message.type,
        "content": message.content,
        "tool_calls": getattr(message, "tool_calls", []),
        "tool_call_id": getattr(message, "tool_call_id", None),
    }


class Observe(BaseCallbackHandler):
    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        event(
            "tool.start",
            name=serialized.get("name"),
            arguments=inputs if inputs is not None else input_str,
            run_id=str(run_id),
            parent_run_id=str(parent_run_id),
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: uuid.UUID,
        parent_run_id: uuid.UUID | None = None,
        **kwargs: Any,
    ) -> None:
        if isinstance(output, Command):
            update = output.update or {}
            value = {
                "todos": update.get("todos"),
                "messages": [message_view(m) for m in update.get("messages", [])],
            }
        elif hasattr(output, "content"):
            value = message_view(output)
        else:
            value = str(output)
        event("tool.end", output=value, run_id=str(run_id), parent_run_id=str(parent_run_id))

    def on_tool_error(self, error: BaseException, *, run_id: uuid.UUID, **kwargs: Any) -> None:
        event("tool.error", error_type=type(error).__name__, run_id=str(run_id))


class DockerFiles(FilesystemBackend, SandboxBackendProtocol):
    @property
    def id(self) -> str:
        return "parity-inspector"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        if command != "python3 /work/analyze.py":
            raise PermissionError("qualification sandbox permits only the pinned analysis command")
        container = f"agent-parity-{uuid.uuid4().hex[:12]}"
        args = [
            "docker",
            "create",
            "--name",
            container,
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
            f"type=bind,src={WORK / 'files'},dst=/work,readonly",
            "--entrypoint",
            "python3",
            os.environ["PARITY_SANDBOX_IMAGE"],
            "/work/analyze.py",
        ]
        created = subprocess.run(args, check=True, capture_output=True, text=True, timeout=30)
        event("sandbox.created", container_id=created.stdout.strip(), command=command)
        try:
            started = subprocess.run(
                ["docker", "start", "--attach", container],
                capture_output=True,
                text=True,
                timeout=30,
            )
            inspected = json.loads(
                subprocess.check_output(["docker", "inspect", container], text=True)
            )[0]
            event(
                "sandbox.finished",
                container_id=inspected["Id"],
                image=inspected["Image"],
                state=inspected["State"],
                host_config={
                    k: inspected["HostConfig"][k]
                    for k in [
                        "NetworkMode",
                        "ReadonlyRootfs",
                        "CapDrop",
                        "SecurityOpt",
                        "Memory",
                        "NanoCpus",
                        "PidsLimit",
                    ]
                },
                user=inspected["Config"]["User"],
                output=started.stdout,
                stderr=started.stderr,
            )
            return ExecuteResponse(
                output=started.stdout + started.stderr, exit_code=inspected["State"]["ExitCode"]
            )
        finally:
            subprocess.run(
                ["docker", "rm", "--force", container], check=True, capture_output=True, timeout=30
            )
            event("sandbox.removed", name=container)


def profile(exclude: set[str]) -> None:
    register_harness_profile(
        f"openai:{SCENARIO['model']}",
        HarnessProfile(
            excluded_tools=frozenset(exclude),
            excluded_middleware=frozenset({"SummarizationMiddleware"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        ),
    )


def main() -> None:
    event(
        "process.start",
        framework=FRAMEWORK,
        resources=resources(),
        sqlite_version=sqlite3.sqlite_version,
    )
    factory = create_deep_agent if FRAMEWORK == "deepagents" else compose
    model = ChatOpenAI(
        model=SCENARIO["model"],
        api_key=SecretStr("local-parity"),
        base_url=os.environ["OPENAI_BASE_URL"],
        use_responses_api=False,
        max_retries=0,
        timeout=120,
    )
    hidden = {"ls", "edit_file", "delete", "glob", "grep"}
    profile(hidden)
    child = factory(
        model=model,
        system_prompt=SCENARIO["child_system"],
        backend=DockerFiles(root_dir=WORK / "files", virtual_mode=True),
        name="inspector",
    )
    profile(hidden)
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=WORK / "files", virtual_mode=True),
        routes={"/memories/": FilesystemBackend(root_dir=WORK / "memory", virtual_mode=True)},
    )
    config: RunnableConfig = {
        "configurable": {"thread_id": "parent"},
        "recursion_limit": 80,
        "max_concurrency": 2,
        "callbacks": [Observe()],
    }
    with closing(
        sqlite3.connect(str(WORK / "checkpoint.sqlite"), check_same_thread=False)
    ) as connection:
        saver = SqliteSaver(connection)
        saver.conn.execute("PRAGMA synchronous=FULL")
        agent = factory(
            model=model,
            system_prompt=SCENARIO["parent_system"],
            backend=backend,
            subagents=[
                {
                    "name": "inspector",
                    "description": "Inspect the input file in a sandbox and return its JSON result.",
                    "runnable": child,
                }
            ],
            memory=["/memories/AGENTS.md"],
            middleware=[TodoListMiddleware()],
            checkpointer=saver,
            interrupt_on={"read_file": True},
            name="parent",
        )
        state = agent.get_state(config)
        event("checkpoint.loaded", next=list(state.next), todos=state.values.get("todos", []))
        event("agent.ready", role="parent", resources=resources())
        request: Any = (
            {"messages": [{"role": "user", "content": SCENARIO["input_prompt"]}]}
            if PHASE == "prepare"
            else Command(resume={"decisions": [{"type": "approve"}]})
        )
        for approval_round in range(8):
            for namespace, mode, update in agent.stream(
                request, config=config, stream_mode=["updates"], subgraphs=True, durability="sync"
            ):
                if not isinstance(update, dict):
                    raise TypeError("expected graph update mapping")
                summary = {}
                for node, values in update.items():
                    if isinstance(values, dict):
                        summary[node] = {
                            "todos": values.get("todos"),
                            "messages": [message_view(m) for m in values.get("messages", [])],
                        }
                    else:
                        summary[node] = str(values)
                event("graph.update", namespace=list(namespace), update=summary)
            state = agent.get_state(config)
            interrupts = [interrupt for task in state.tasks for interrupt in task.interrupts]
            if not interrupts:
                if PHASE == "prepare":
                    raise AssertionError(
                        "completed without the required durable-memory read boundary"
                    )
                event(
                    "process.completed",
                    todos=state.values.get("todos", []),
                    messages=[message_view(m) for m in state.values["messages"]],
                    resources=resources(),
                )
                print(
                    json.dumps({"status": "completed", "todos": state.values.get("todos", [])}),
                    flush=True,
                )
                return
            actions = interrupts[0].value["action_requests"]
            event("approval.pending", actions=actions, todos=state.values.get("todos", []))
            memory_read = any(
                a["name"] == "read_file" and a["args"].get("file_path") == "/memories/result.json"
                for a in actions
            )
            if PHASE == "prepare" and memory_read:
                if (
                    not (WORK / "memory/result.json").exists()
                    or (WORK / "files/report.json").exists()
                ):
                    raise AssertionError("invalid pause boundary")
                event("process.paused", todos=state.values.get("todos", []), resources=resources())
                print(
                    json.dumps({"status": "paused", "todos": state.values.get("todos", [])}),
                    flush=True,
                )
                sys.stdin.readline()  # Hold the open database until the driver sends SIGKILL.
                return
            request = Command(resume={"decisions": [{"type": "approve"} for _ in actions]})
        raise RuntimeError("approval round budget exceeded")


if __name__ == "__main__":
    main()
