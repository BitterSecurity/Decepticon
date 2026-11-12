"""Scheduled engagement runs via LangGraph native cron.

A schedule starts an engagement run on a cron expression. Two modes:

* ``reassess`` — each tick starts a FRESH thread (periodic re-assessment /
  continuous monitoring). Pair it with a baseline diff downstream to report
  only what changed.
* ``campaign`` — each tick continues the SAME thread (a long-running
  engagement that progresses across scheduled slots). Combined with
  ``TimeWindowMiddleware`` and ``time-window-resume``, a campaign only works
  inside its RoE time window and resumes across pauses.

This is a thin wrapper over the LangGraph server's cron API; the server owns
the scheduler. ``metadata.decepticon_schedule`` tags schedules this module
created so :func:`list_schedules` can report them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

log = logging.getLogger(__name__)

Mode = Literal["reassess", "campaign"]
_TAG = "decepticon_schedule"


@dataclass(frozen=True)
class Schedule:
    cron_id: str
    assistant_id: str
    schedule: str
    mode: str
    engagement: str | None
    thread_id: str | None
    next_run_date: str | None


def _run_input(prompt: str, workspace_path: str | None) -> dict[str, Any]:
    state: dict[str, Any] = {"messages": [{"role": "user", "content": prompt}]}
    if workspace_path:
        state["workspace_path"] = workspace_path
    return state


def _run_config(workspace_path: str | None, engagement: str | None) -> dict[str, Any] | None:
    configurable: dict[str, Any] = {}
    if workspace_path:
        configurable["workspace_path"] = workspace_path
    if engagement:
        configurable["engagement_name"] = engagement
    return {"configurable": configurable} if configurable else None


async def create_schedule(
    client: Any,
    *,
    assistant_id: str,
    schedule: str,
    prompt: str,
    mode: Mode = "reassess",
    engagement: str | None = None,
    workspace_path: str | None = None,
    thread_id: str | None = None,
    multitask_strategy: str = "reject",
    end_time: str | None = None,
) -> Schedule:
    """Create a cron schedule for an engagement run.

    ``campaign`` mode runs against one thread (created when ``thread_id`` is
    omitted) so every tick continues it; ``reassess`` mode leaves the thread
    out so each tick is fresh. ``multitask_strategy`` defaults to ``reject`` so
    a still-running engagement is never doubled up.
    """
    metadata = {
        _TAG: {
            "mode": mode,
            "engagement": engagement,
            "workspace_path": workspace_path,
        }
    }
    run_input = _run_input(prompt, workspace_path)
    config = _run_config(workspace_path, engagement)
    kwargs: dict[str, Any] = {
        "schedule": schedule,
        "input": run_input,
        "metadata": metadata,
        "multitask_strategy": multitask_strategy,
    }
    if config is not None:
        kwargs["config"] = config
    if end_time is not None:
        kwargs["end_time"] = end_time

    if mode == "campaign":
        if thread_id is None:
            thread = await client.threads.create(metadata=metadata)
            thread_id = thread["thread_id"]
        cron = await client.crons.create_for_thread(thread_id, assistant_id, **kwargs)
    else:
        cron = await client.crons.create(assistant_id, **kwargs)
        thread_id = None
    log.info("schedule: created cron %s (%s, %s)", cron.get("cron_id"), assistant_id, mode)
    return Schedule(
        cron_id=cron["cron_id"],
        assistant_id=assistant_id,
        schedule=schedule,
        mode=mode,
        engagement=engagement,
        thread_id=thread_id,
        next_run_date=cron.get("next_run_date"),
    )


def _schedule_from_cron(cron: dict[str, Any]) -> Schedule | None:
    tag = (cron.get("metadata") or {}).get(_TAG)
    if not isinstance(tag, dict):
        return None
    return Schedule(
        cron_id=cron["cron_id"],
        assistant_id=cron.get("assistant_id", ""),
        schedule=cron.get("schedule", ""),
        mode=tag.get("mode", ""),
        engagement=tag.get("engagement"),
        thread_id=cron.get("thread_id"),
        next_run_date=cron.get("next_run_date"),
    )


async def list_schedules(client: Any) -> list[Schedule]:
    """Engagement schedules created by this module, newest page first."""
    schedules: list[Schedule] = []
    offset = 0
    while True:
        page = await client.crons.search(limit=100, offset=offset)
        for cron in page:
            parsed = _schedule_from_cron(cron)
            if parsed is not None:
                schedules.append(parsed)
        if len(page) < 100:
            return schedules
        offset += 100


async def delete_schedule(client: Any, cron_id: str) -> None:
    await client.crons.delete(cron_id)
    log.info("schedule: deleted cron %s", cron_id)
