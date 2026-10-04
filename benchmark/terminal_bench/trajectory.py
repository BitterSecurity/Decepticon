from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import TypeAdapter

from decepticon import __version__
from decepticon.tools.bash import BASH_TOOLS

AGENT_NAME = "decepticon-bash"


@dataclass(frozen=True, slots=True)
class RunMetrics:
    reasoning_effort: str
    input_tokens: int
    cache_tokens: int
    output_tokens: int


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _tool_definitions() -> list[dict[str, object]]:
    definitions: list[dict[str, object]] = []
    for tool in BASH_TOOLS:
        args_schema = tool.args_schema
        if isinstance(args_schema, dict):
            schema = args_schema
        elif args_schema is not None:
            schema = TypeAdapter(args_schema).json_schema()
        else:
            schema = {}
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": schema,
                },
            }
        )
    return definitions


def _step(step_id: int, message: BaseMessage, model_name: str | None) -> Step:
    if isinstance(message, HumanMessage):
        return Step(step_id=step_id, source="user", message=_message_text(message))
    if isinstance(message, SystemMessage):
        return Step(step_id=step_id, source="system", message=_message_text(message))
    if isinstance(message, AIMessage):
        calls = [
            ToolCall(
                tool_call_id=call.get("id") or f"call-{step_id}-{index}",
                function_name=call["name"],
                arguments=call["args"],
            )
            for index, call in enumerate(message.tool_calls)
        ]
        return Step(
            step_id=step_id,
            source="agent",
            message=_message_text(message),
            model_name=model_name,
            tool_calls=calls or None,
            llm_call_count=1,
        )
    return Step(step_id=step_id, source="system", message=_message_text(message))


def _steps(messages: Sequence[BaseMessage], model_name: str | None) -> list[Step]:
    steps: list[Step] = []
    for message in messages:
        if isinstance(message, ToolMessage) and steps and steps[-1].tool_calls:
            previous = steps[-1]
            results = list(previous.observation.results) if previous.observation else []
            results.append(
                ObservationResult(
                    source_call_id=message.tool_call_id,
                    content=_message_text(message),
                )
            )
            steps[-1] = previous.model_copy(update={"observation": Observation(results=results)})
            continue
        steps.append(_step(len(steps) + 1, message, model_name))
    return steps


def write_atif_trajectory(
    *,
    logs_dir: Path,
    messages: Sequence[BaseMessage],
    model_name: str | None,
    session_id: str | None,
    metrics: RunMetrics,
    system_prompt: str | None = None,
) -> Path:
    source_messages = (
        [SystemMessage(content=system_prompt), *messages] if system_prompt else messages
    )
    steps = _steps(source_messages, model_name)
    trajectory = Trajectory(
        session_id=session_id,
        agent=Agent(
            name=AGENT_NAME,
            version=__version__,
            model_name=model_name,
            tool_definitions=_tool_definitions(),
            extra={
                "tool_surface": "decepticon.tools.bash.BASH_TOOLS",
                "reasoning_effort": metrics.reasoning_effort,
            },
        ),
        steps=steps,
        final_metrics=FinalMetrics(
            total_prompt_tokens=metrics.input_tokens,
            total_completion_tokens=metrics.output_tokens,
            total_cached_tokens=metrics.cache_tokens,
            total_steps=len(steps),
        ),
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "trajectory.json"
    path.write_text(
        json.dumps(trajectory.to_json_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


__all__ = ["AGENT_NAME", "RunMetrics", "write_atif_trajectory"]
