"""time-window-resume selects and resumes only due, time-window-paused threads."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from decepticon.middleware.time_window import PAUSE_KIND
from decepticon.runtime.time_window_resume import list_paused, resume_due

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def _thread(tid: str, resume_at, kind=PAUSE_KIND, graph="recon") -> dict:
    return {
        "thread_id": tid,
        "metadata": {"graph_id": graph},
        "interrupts": {"task": [{"id": "i", "value": {"kind": kind, "resume_at": resume_at}}]},
    }


class _Threads:
    def __init__(self, threads):
        self.threads = threads
        self.calls = []

    async def search(self, *, status, limit, offset):
        self.calls.append((status, limit, offset))
        return self.threads[offset : offset + limit]


class _Runs:
    def __init__(self):
        self.created = []

    async def create(self, thread_id, assistant_id, *, command):
        self.created.append((thread_id, assistant_id, command))


class _Client:
    def __init__(self, threads):
        self.threads = _Threads(threads)
        self.runs = _Runs()


def test_only_due_time_window_pauses_are_resumed() -> None:
    client = _Client(
        [
            _thread("due", (NOW - timedelta(minutes=1)).isoformat()),
            _thread("later", (NOW + timedelta(hours=1)).isoformat()),
            _thread("no-window", None),
            _thread("hitl", (NOW - timedelta(minutes=1)).isoformat(), kind="other"),
        ]
    )
    resumed = asyncio.run(resume_due(client, NOW))
    assert [r.thread_id for r in resumed] == ["due"]
    assert client.runs.created == [("due", "recon", {"resume": {"resumed_at": NOW.isoformat()}})]
    assert client.threads.calls[0][0] == "interrupted"


def test_listing_pages_through_all_threads() -> None:
    threads = [_thread(f"t{i}", NOW.isoformat()) for i in range(150)]
    client = _Client(threads)
    assert len(asyncio.run(list_paused(client))) == 150
    assert [c[2] for c in client.threads.calls] == [0, 100]
