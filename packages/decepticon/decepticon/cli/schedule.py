"""``decepticon-cli schedule`` — schedule engagement runs on a LangGraph server."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from decepticon.runtime.engagement_schedule import (
    create_schedule,
    delete_schedule,
    list_schedules,
)


def _client(url: str):
    from langgraph_sdk import get_client

    return get_client(url=url)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decepticon-cli schedule",
        description="Schedule engagement runs via the LangGraph server's cron.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DECEPTICON_API_URL", "http://localhost:2024"),
        help="LangGraph server URL (default: $DECEPTICON_API_URL or http://localhost:2024)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a schedule")
    create.add_argument("--assistant", required=True, help="Graph / assistant id (e.g. recon)")
    create.add_argument("--schedule", required=True, help="Cron expression, e.g. '0 2 * * 1'")
    create.add_argument("--prompt", required=True, help="Task prompt sent on each run")
    create.add_argument(
        "--mode",
        choices=("reassess", "campaign"),
        default="reassess",
        help="reassess = fresh thread per run; campaign = continue one thread",
    )
    create.add_argument("--engagement", help="Engagement slug (recorded on the schedule)")
    create.add_argument("--workspace", help="Engagement workspace path (e.g. /workspace/<slug>)")
    create.add_argument("--thread", help="Existing thread id for campaign mode")
    create.add_argument(
        "--multitask",
        choices=("reject", "enqueue", "interrupt", "rollback"),
        default="reject",
        help="What to do when a run is still active at the next tick (default: reject)",
    )
    create.add_argument("--end-time", help="ISO timestamp after which the schedule stops")

    sub.add_parser("list", help="List schedules created by this tool")

    delete = sub.add_parser("delete", help="Delete a schedule")
    delete.add_argument("cron_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    client = _client(args.url)

    if args.command == "create":
        schedule = asyncio.run(
            create_schedule(
                client,
                assistant_id=args.assistant,
                schedule=args.schedule,
                prompt=args.prompt,
                mode=args.mode,
                engagement=args.engagement,
                workspace_path=args.workspace,
                thread_id=args.thread,
                multitask_strategy=args.multitask,
                end_time=args.end_time,
            )
        )
        print(
            json.dumps(
                {
                    "cron_id": schedule.cron_id,
                    "mode": schedule.mode,
                    "assistant_id": schedule.assistant_id,
                    "thread_id": schedule.thread_id,
                    "next_run_date": schedule.next_run_date,
                },
                indent=2,
            )
        )
        return 0
    if args.command == "list":
        schedules = asyncio.run(list_schedules(client))
        print(
            json.dumps(
                [
                    {
                        "cron_id": s.cron_id,
                        "assistant_id": s.assistant_id,
                        "schedule": s.schedule,
                        "mode": s.mode,
                        "engagement": s.engagement,
                        "thread_id": s.thread_id,
                        "next_run_date": s.next_run_date,
                    }
                    for s in schedules
                ],
                indent=2,
            )
        )
        return 0
    asyncio.run(delete_schedule(client, args.cron_id))
    print(json.dumps({"deleted": args.cron_id}))
    return 0
