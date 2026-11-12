"""Resume runs paused by ``TimeWindowMiddleware`` once their window opens.

A paused run is an interrupted LangGraph thread whose pending interrupt
payload has ``kind == "decepticon.time_window"`` and a ``resume_at``
timestamp. :func:`resume_due` finds those threads on a LangGraph server and,
for each one whose ``resume_at`` has passed, starts a run with
``Command(resume=...)``. The middleware re-checks the window when it resumes,
so an early or repeated resume only pauses again.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from decepticon.middleware.time_window import PAUSE_KIND

log = logging.getLogger(__name__)

_PAGE = 100


@dataclass(frozen=True)
class PausedRun:
    thread_id: str
    assistant_id: str
    resume_at: datetime | None
    payload: dict[str, Any]


def _parse(ts: Any) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        value = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _paused_payloads(thread: dict[str, Any]) -> list[dict[str, Any]]:
    interrupts = thread.get("interrupts") or {}
    found: list[dict[str, Any]] = []
    for items in interrupts.values() if isinstance(interrupts, dict) else []:
        for item in items or []:
            value = item.get("value") if isinstance(item, dict) else None
            if isinstance(value, dict) and value.get("kind") == PAUSE_KIND:
                found.append(value)
    return found


def _assistant_id(thread: dict[str, Any]) -> str | None:
    metadata = thread.get("metadata") or {}
    for key in ("assistant_id", "graph_id"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def list_paused(client: Any) -> list[PausedRun]:
    """All threads currently paused by the time window."""
    paused: list[PausedRun] = []
    offset = 0
    while True:
        page = await client.threads.search(status="interrupted", limit=_PAGE, offset=offset)
        for thread in page:
            payloads = _paused_payloads(thread)
            assistant = _assistant_id(thread)
            if not payloads or assistant is None:
                continue
            latest = payloads[-1]
            paused.append(
                PausedRun(
                    thread_id=thread["thread_id"],
                    assistant_id=assistant,
                    resume_at=_parse(latest.get("resume_at")),
                    payload=latest,
                )
            )
        if len(page) < _PAGE:
            return paused
        offset += _PAGE


async def resume_due(client: Any, now: datetime | None = None) -> list[PausedRun]:
    """Resume every paused run whose ``resume_at`` has passed."""
    now = now or datetime.now(timezone.utc)
    resumed: list[PausedRun] = []
    for run in await list_paused(client):
        if run.resume_at is None or run.resume_at > now:
            continue
        await client.runs.create(
            run.thread_id,
            run.assistant_id,
            command={"resume": {"resumed_at": now.isoformat()}},
        )
        log.info("time window: resumed thread %s (%s)", run.thread_id, run.assistant_id)
        resumed.append(run)
    return resumed


async def watch(client: Any, interval_s: float = 30.0) -> None:
    """Resume due runs forever, every ``interval_s`` seconds."""
    while True:
        try:
            await resume_due(client)
        except Exception as exc:  # noqa: BLE001 - keep the resumer alive
            log.warning("time window resume pass failed: %s", exc)
        await asyncio.sleep(interval_s)
