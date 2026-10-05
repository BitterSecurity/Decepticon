"""Drive Decepticon engagements through the LangGraph SDK.

A thin async bridge: MCP tool calls are translated into LangGraph runs
against a running Decepticon server, reusing the same scope/state payload
shape as ``decepticon.cli.scan`` so the orchestrator (and its RoE
enforcement middleware) sees an identical engagement contract whether the
trigger is the CLI or an external agent.

Everything is keyed by ``thread_id`` (the engagement handle) — the active
run is resolved via ``runs.list`` exactly as the web/CLI live observer does,
so callers never juggle run ids. Turns dispatch as background runs so a long
red-team run never blocks the calling agent's tool call.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from decepticon.mcp_server.config import ServerConfig
from decepticon.mcp_server.models import (
    EngagementSummary,
    GraphInfo,
    RunHandle,
    ScanMode,
    StartResult,
    StreamEvent,
)
from decepticon.mcp_server.streaming import watch_run
from decepticon_core.utils.logging import get_logger

log = get_logger("mcp_server.engagements")

_ACTIVE_STATUSES = frozenset({"pending", "running"})


def _split_model_command(message: str) -> tuple[str | None, str]:
    """Pull a leading ``/model <id>`` directive off an operator message.

    Mirrors the CLI's client-side slash command: the override travels to the
    server in ``config.configurable.model_override`` (the path
    ``ModelOverrideMiddleware`` reads), not as literal chat text. Returns
    ``(model_override, remaining_message)``:

      ``/model anthropic/claude-opus-4-8 keep going`` -> ``("…opus-4-8", "keep going")``
      ``/model openai/gpt-5.5``                       -> ``("…gpt-5.5", "")``
      ``focus on the API`` / ``/model`` (no id)        -> ``(None, <original>)``
    """
    parts = message.lstrip().split(maxsplit=2)
    if len(parts) < 2 or parts[0].lower() != "/model":
        return None, message
    return parts[1], (parts[2] if len(parts) > 2 else "")


class EngagementClient:
    """Async bridge from MCP tool calls to a running Decepticon LangGraph server.

    ``client`` is the ``langgraph_sdk`` async client. It is injectable so unit
    tests can pass a fake; in production it is created lazily from the
    configured URL.
    """

    def __init__(self, config: ServerConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            from langgraph_sdk import get_client

            self._client = get_client(url=self._config.langgraph_url)
        return self._client

    async def list_graphs(self) -> list[GraphInfo]:
        """Return the engagement graphs (assistants) the connected server exposes."""
        client = self._ensure_client()
        assistants = await client.assistants.search()
        return [
            GraphInfo(
                assistant_id=str(entry.get("assistant_id", "")),
                graph_id=str(entry.get("graph_id", "")),
                name=str(entry.get("name") or entry.get("graph_id") or ""),
            )
            for entry in assistants
        ]

    async def list_engagements(self, *, limit: int) -> list[EngagementSummary]:
        """List recent engagement threads (most-recently-updated first)."""
        client = self._ensure_client()
        threads = await client.threads.search(limit=limit, sort_by="updated_at", sort_order="desc")
        summaries: list[EngagementSummary] = []
        for thread in threads:
            values = thread.get("values") or {}
            name = values.get("engagement_name") if isinstance(values, dict) else None
            summaries.append(
                EngagementSummary(
                    thread_id=str(thread.get("thread_id", "")),
                    engagement_name=str(name) if name else None,
                    status=str(thread.get("status", "unknown")),
                    created_at=_opt_str(thread.get("created_at")),
                    updated_at=_opt_str(thread.get("updated_at")),
                )
            )
        return summaries

    async def get_state(self, thread_id: str) -> Any:
        """Return the raw ThreadState for a thread (values + metadata)."""
        client = self._ensure_client()
        return await client.threads.get_state(thread_id)

    async def latest_run(self, thread_id: str) -> dict[str, Any] | None:
        """Return the most recent run dict for a thread, or None when there is none."""
        client = self._ensure_client()
        runs = await client.runs.list(thread_id, limit=1)
        return runs[0] if runs else None

    async def start(
        self,
        *,
        targets: Sequence[str],
        instruction: str,
        scan_mode: ScanMode,
        engagement_name: str,
        assistant: str,
        workspace_path: str,
    ) -> StartResult:
        """Dispatch a background engagement run and return its handle."""
        scope_payload: dict[str, Any] = {
            "targets": list(targets),
            "scope_mode": "full",
            "diff_files": [],
            "scan_mode": scan_mode,
            "instruction": instruction,
        }
        run_config: dict[str, Any] = {
            "configurable": {
                "engagement_name": engagement_name,
                "workspace_path": workspace_path,
                "scan_mode": scan_mode,
            },
        }

        client = self._ensure_client()
        thread = await client.threads.create()
        thread_id = str(thread["thread_id"])
        run = await client.runs.create(
            thread_id,
            assistant_id=assistant,
            input=_engagement_input(scope_payload, engagement_name),
            config=run_config,
        )
        log.info("dispatched engagement %s on thread %s", engagement_name, thread_id)
        return StartResult(
            engagement_name=engagement_name,
            thread_id=thread_id,
            run_id=str(run["run_id"]),
            assistant=assistant,
            status=str(run.get("status", "pending")),
            langgraph_url=self._config.langgraph_url,
        )

    async def send_message(
        self, *, thread_id: str, message: str, assistant: str | None = None
    ) -> RunHandle:
        """Send an operator message onto an existing engagement thread.

        Continues the conversation (steer focus, answer the orchestrator, or use
        ``/model <id>`` to switch models). The turn is enqueued after any active
        run so it is never rejected, and dispatched in the background.
        """
        client = self._ensure_client()
        resolved = assistant
        if resolved is None:
            latest = await self.latest_run(thread_id)
            resolved = str(latest.get("assistant_id")) if latest else self._config.default_assistant

        override, body = _split_model_command(message)
        create_kwargs: dict[str, Any] = {
            "assistant_id": resolved,
            "multitask_strategy": "enqueue",
        }
        if override is None:
            create_kwargs["input"] = {"messages": [{"role": "user", "content": message}]}
        else:
            # ``/model <id>`` switches the orchestrator's model for this turn via
            # the same config field the CLI uses; any trailing text is the turn's
            # actual message (a bare ``/model <id>`` just rebinds the model).
            create_kwargs["input"] = {
                "messages": [{"role": "user", "content": body}] if body else []
            }
            create_kwargs["config"] = {"configurable": {"model_override": override}}
        run = await client.runs.create(thread_id, **create_kwargs)
        return RunHandle(
            thread_id=thread_id,
            run_id=str(run["run_id"]),
            assistant=resolved,
            status=str(run.get("status", "pending")),
        )

    async def cancel(self, thread_id: str) -> str | None:
        """Cancel the active run on a thread. Returns the cancelled run id, if any."""
        latest = await self.latest_run(thread_id)
        if latest is None or str(latest.get("status")) not in _ACTIVE_STATUSES:
            return None
        run_id = str(latest["run_id"])
        await self._ensure_client().runs.cancel(thread_id, run_id)
        return run_id

    async def watch(
        self, *, thread_id: str, run_id: str, max_seconds: float, max_events: int
    ) -> tuple[list[StreamEvent], bool]:
        """Tail a run's live stream, bounded by time and event count."""
        return await watch_run(
            self._ensure_client(),
            thread_id=thread_id,
            run_id=run_id,
            max_seconds=max_seconds,
            max_events=max_events,
        )


def _engagement_input(scope_payload: dict[str, Any], engagement_name: str) -> dict[str, Any]:
    return {
        "messages": [
            {
                "role": "user",
                "content": (
                    "Run an authorized security engagement. Scope and rules of "
                    "engagement are attached as JSON:\n\n" + json.dumps(scope_payload, indent=2)
                ),
            }
        ],
        "engagement_name": engagement_name,
        "scan_scope": scope_payload,
    }


def _opt_str(value: Any) -> str | None:
    return str(value) if value else None
