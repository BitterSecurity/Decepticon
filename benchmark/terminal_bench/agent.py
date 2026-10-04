from __future__ import annotations

import logging
from pathlib import Path
from typing import Final, Literal

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from pydantic import TypeAdapter

from benchmark.terminal_bench.sandbox import HarborSandboxAdapter
from benchmark.terminal_bench.trajectory import AGENT_NAME, RunMetrics, write_atif_trajectory
from decepticon import __version__
from decepticon.llm.factory import LLMFactory
from decepticon.middleware.notifications import SandboxNotificationMiddleware
from decepticon.runtime.recording import RecordingMiddleware
from decepticon.tools.bash import BASH_PROMPT, BASH_TOOLS
from decepticon.tools.bash.bash import set_sandbox
from decepticon_core.types.llm import LLMModelMapping, ModelAssignment

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max", "ultra"]
_REASONING_EFFORT: Final = TypeAdapter(ReasoningEffort)

SYSTEM_PROMPT = f"""\
You are Decepticon's dedicated terminal operator running Terminal-Bench 2.1.
Complete the user's task inside the provided isolated environment. Use the bash
tool as your execution surface. You may create Python or other programs through
bash when useful, but do not merely explain the solution: execute it and verify
the resulting files or state. Treat the user instruction as the full authorized
scope for this benchmark task. Stop when the requested result is verified.

{BASH_PROMPT}

Terminal-Bench adapter note: this benchmark intentionally exposes only the four
bash tools above. There is no write_file, edit_file, read_file, grep, or glob
tool. Create, inspect, and modify files with bounded shell commands through bash.
"""


class DecepticonTerminalBenchAgent(BaseAgent):
    SUPPORTS_ATIF = True

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: logging.Logger | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        skills_dir: str | None = None,
        *,
        extra_env: dict[str, str] | None = None,
        reasoning_effort: str = "medium",
    ) -> None:
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            extra_env=extra_env,
        )
        self.reasoning_effort = _REASONING_EFFORT.validate_python(reasoning_effort)

    @staticmethod
    def name() -> str:
        return AGENT_NAME

    def version(self) -> str | None:
        return __version__

    @property
    def tools(self) -> list[BaseTool]:
        return BASH_TOOLS

    async def setup(self, environment: BaseEnvironment) -> None:
        result = await environment.exec("mkdir -p /workspace /tmp", user="root")
        if result.return_code != 0:
            raise RuntimeError(result.stderr or "failed to prepare benchmark environment")

    def _model(self) -> BaseChatModel:
        if not self.model_name:
            model = LLMFactory().get_model("exploiter")
        else:
            mapping = LLMModelMapping(
                assignments={
                    "terminal_bench": ModelAssignment(primary=self.model_name, temperature=0.0)
                }
            )
            model = LLMFactory(mapping=mapping, apply_role_overrides=False).get_model(
                "terminal_bench"
            )
        return model.model_copy(update={"reasoning_effort": self.reasoning_effort})

    @staticmethod
    def _usage(messages: list[BaseMessage]) -> tuple[int, int, int]:
        input_tokens = 0
        cache_tokens = 0
        output_tokens = 0
        for message in messages:
            if not isinstance(message, AIMessage) or message.usage_metadata is None:
                continue
            input_tokens += message.usage_metadata.get("input_tokens", 0)
            output_tokens += message.usage_metadata.get("output_tokens", 0)
            input_details = message.usage_metadata.get("input_token_details")
            if input_details is not None:
                cache_tokens += input_details.get("cache_read", 0)
        return input_tokens, cache_tokens, output_tokens

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        record_path = self.logs_dir / "decepticon-runtime.jsonl"
        messages: list[BaseMessage] = []
        sandbox = HarborSandboxAdapter(environment)
        set_sandbox(sandbox)
        graph = create_agent(
            self._model(),
            tools=self.tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=[
                SandboxNotificationMiddleware(sandbox=sandbox),
                RecordingMiddleware(path=record_path),
            ],
            name=AGENT_NAME,
        ).with_config({"recursion_limit": 1000})
        result = await graph.ainvoke({"messages": [{"role": "user", "content": instruction}]})
        messages = list(result["messages"])

        session_id = str(self.context_id) if self.context_id is not None else self.session_id
        input_tokens, cache_tokens, output_tokens = self._usage(messages)
        metrics = RunMetrics(
            reasoning_effort=self.reasoning_effort,
            input_tokens=input_tokens,
            cache_tokens=cache_tokens,
            output_tokens=output_tokens,
        )
        write_atif_trajectory(
            logs_dir=self.logs_dir,
            messages=messages,
            model_name=self.model_name,
            session_id=session_id,
            metrics=metrics,
            system_prompt=SYSTEM_PROMPT,
        )
        context.n_input_tokens = input_tokens or None
        context.n_cache_tokens = cache_tokens or None
        context.n_output_tokens = output_tokens or None
        context.metadata = {
            "reasoning_effort": self.reasoning_effort,
            "runtime_record": record_path.name,
            "tool_surface": "decepticon.tools.bash.BASH_TOOLS",
        }


__all__ = ["AGENT_NAME", "DecepticonTerminalBenchAgent"]
