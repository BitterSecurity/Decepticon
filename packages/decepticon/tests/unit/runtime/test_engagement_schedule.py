"""Engagement cron schedule helper: mode wiring, tagging, listing."""

from __future__ import annotations

import asyncio

from decepticon.runtime.engagement_schedule import (
    create_schedule,
    delete_schedule,
    list_schedules,
)


class _Crons:
    def __init__(self):
        self.created: list[dict] = []
        self.for_thread: list[dict] = []
        self.deleted: list[str] = []
        self.page: list[dict] = []

    async def create(self, assistant_id, **kwargs):
        self.created.append({"assistant_id": assistant_id, **kwargs})
        return {"cron_id": "c-fresh", "next_run_date": "2026-11-16T02:00:00+00:00"}

    async def create_for_thread(self, thread_id, assistant_id, **kwargs):
        self.for_thread.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        return {"cron_id": "c-camp", "next_run_date": "2026-11-16T02:00:00+00:00"}

    async def search(self, *, limit, offset):
        return self.page[offset : offset + limit]

    async def delete(self, cron_id):
        self.deleted.append(cron_id)


class _Threads:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, *, metadata=None):
        self.created.append(metadata)
        return {"thread_id": "t-new"}


class _Client:
    def __init__(self):
        self.crons = _Crons()
        self.threads = _Threads()


def test_reassess_mode_creates_a_threadless_cron() -> None:
    client = _Client()
    sched = asyncio.run(
        create_schedule(
            client,
            assistant_id="recon",
            schedule="0 2 * * 1",
            prompt="weekly recon",
            mode="reassess",
            engagement="acme",
            workspace_path="/workspace/acme",
        )
    )
    assert sched.cron_id == "c-fresh" and sched.thread_id is None
    assert client.crons.for_thread == [] and client.threads.created == []
    call = client.crons.created[0]
    assert call["assistant_id"] == "recon" and call["schedule"] == "0 2 * * 1"
    assert call["multitask_strategy"] == "reject"
    assert call["input"]["workspace_path"] == "/workspace/acme"
    assert call["config"]["configurable"]["workspace_path"] == "/workspace/acme"
    assert call["metadata"]["decepticon_schedule"]["mode"] == "reassess"


def test_campaign_mode_creates_a_thread_and_binds_the_cron_to_it() -> None:
    client = _Client()
    sched = asyncio.run(
        create_schedule(
            client,
            assistant_id="decepticon",
            schedule="*/30 * * * *",
            prompt="continue the campaign",
            mode="campaign",
            engagement="acme",
            workspace_path="/workspace/acme",
        )
    )
    assert sched.thread_id == "t-new" and sched.cron_id == "c-camp"
    assert client.crons.created == []
    assert client.crons.for_thread[0]["thread_id"] == "t-new"
    assert client.threads.created[0]["decepticon_schedule"]["mode"] == "campaign"


def test_campaign_mode_reuses_an_explicit_thread() -> None:
    client = _Client()
    asyncio.run(
        create_schedule(
            client,
            assistant_id="decepticon",
            schedule="*/30 * * * *",
            prompt="continue",
            mode="campaign",
            thread_id="t-existing",
        )
    )
    assert client.threads.created == []
    assert client.crons.for_thread[0]["thread_id"] == "t-existing"


def test_list_reports_only_tagged_schedules_and_pages() -> None:
    client = _Client()
    client.crons.page = [
        {
            "cron_id": f"c{i}",
            "assistant_id": "recon",
            "schedule": "0 2 * * 1",
            "metadata": {"decepticon_schedule": {"mode": "reassess", "engagement": "acme"}},
        }
        for i in range(100)
    ] + [
        {"cron_id": "other", "assistant_id": "x", "schedule": "* * * * *", "metadata": {}},
    ]
    schedules = asyncio.run(list_schedules(client))
    assert len(schedules) == 100
    assert all(s.mode == "reassess" for s in schedules)


def test_delete_calls_through() -> None:
    client = _Client()
    asyncio.run(delete_schedule(client, "c1"))
    assert client.crons.deleted == ["c1"]
