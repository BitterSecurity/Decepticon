from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from anyio.to_thread import run_sync
from harbor.environments.base import BaseEnvironment
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from benchmark.terminal_bench.agent import AGENT_NAME, DecepticonTerminalBenchAgent
from benchmark.terminal_bench.sandbox import HarborSandboxAdapter
from benchmark.terminal_bench.trajectory import RunMetrics, write_atif_trajectory
from decepticon.tools.bash import BASH_TOOLS


@pytest.mark.asyncio
async def test_sandbox_adapter_executes_inside_harbor_environment() -> None:
    class Environment:
        async def exec(self, command: str, *, timeout_sec: int | None = None):
            assert command == "printf ready"
            assert timeout_sec == 5
            return SimpleNamespace(return_code=0, stdout="ready", stderr="")

    adapter = HarborSandboxAdapter(cast(BaseEnvironment, Environment()))
    result = await run_sync(lambda: adapter.execute("printf ready", timeout=5))

    assert result.output == "ready"
    assert result.exit_code == 0


def test_agent_exposes_the_production_bash_tool_objects(tmp_path: Path) -> None:
    agent = DecepticonTerminalBenchAgent(
        logs_dir=tmp_path,
        model_name="test/model",
        reasoning_effort="max",
    )

    assert agent.tools == BASH_TOOLS
    assert all(actual is expected for actual, expected in zip(agent.tools, BASH_TOOLS, strict=True))
    assert agent.reasoning_effort == "max"


def test_agent_applies_reasoning_effort_to_the_model(tmp_path: Path) -> None:
    agent = DecepticonTerminalBenchAgent(
        logs_dir=tmp_path,
        model_name="auth/gpt-5.6-luna",
        reasoning_effort="max",
    )

    model = agent._model()

    assert model.model_dump().get("reasoning_effort") == "max"


def test_usage_counts_cache_tokens() -> None:
    message = AIMessage(
        content="Done",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "input_token_details": {"cache_read": 30},
        },
    )

    assert DecepticonTerminalBenchAgent._usage([message]) == (100, 30, 20)


def test_atif_export_preserves_tool_calls_and_observations(tmp_path: Path) -> None:
    messages = [
        HumanMessage(content="Create /root/answer.txt"),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "bash",
                    "args": {
                        "command": "touch /root/answer.txt",
                        "description": "Create the answer file",
                    },
                }
            ],
        ),
        ToolMessage(content="created", tool_call_id="call-1", name="bash"),
        AIMessage(content="Done"),
    ]

    path = write_atif_trajectory(
        logs_dir=tmp_path,
        messages=messages,
        model_name="test/model",
        session_id="trial-1",
        metrics=RunMetrics(
            reasoning_effort="max",
            input_tokens=100,
            cache_tokens=30,
            output_tokens=20,
        ),
    )

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == "ATIF-v1.7"
    assert payload["agent"]["name"] == AGENT_NAME
    assert payload["agent"]["extra"]["reasoning_effort"] == "max"
    assert payload["final_metrics"] == {
        "total_prompt_tokens": 100,
        "total_completion_tokens": 20,
        "total_cached_tokens": 30,
        "total_steps": 3,
    }
    assert payload["steps"][1]["tool_calls"][0]["function_name"] == "bash"
    assert payload["steps"][1]["observation"]["results"][0] == {
        "source_call_id": "call-1",
        "content": "created",
    }
