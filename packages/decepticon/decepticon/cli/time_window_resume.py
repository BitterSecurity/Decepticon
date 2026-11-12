"""``decepticon-cli time-window-resume`` — resume runs paused by the time window."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os

from decepticon.runtime.time_window_resume import list_paused, resume_due, watch


def _client(url: str):
    from langgraph_sdk import get_client

    return get_client(url=url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="decepticon-cli time-window-resume",
        description="Resume engagement runs paused outside their RoE time window.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("DECEPTICON_API_URL", "http://localhost:2024"),
        help="LangGraph server URL (default: $DECEPTICON_API_URL or http://localhost:2024)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Keep running and resume when due")
    mode.add_argument("--list", action="store_true", help="List paused runs and exit")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between passes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = _client(args.url)

    if args.list:
        paused = asyncio.run(list_paused(client))
        print(
            json.dumps(
                [
                    {
                        "thread_id": p.thread_id,
                        "assistant_id": p.assistant_id,
                        "resume_at": p.resume_at.isoformat() if p.resume_at else None,
                        "code": p.payload.get("code"),
                    }
                    for p in paused
                ],
                indent=2,
            )
        )
        return 0
    if args.watch:
        asyncio.run(watch(client, interval_s=args.interval))
        return 0
    resumed = asyncio.run(resume_due(client))
    print(json.dumps({"resumed": [r.thread_id for r in resumed]}))
    return 0
