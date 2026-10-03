from __future__ import annotations

import json
import os
import re

import httpx
from langchain_core.tools import tool


def _url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


@tool
def blue_sensor_scan(limit: int = 50) -> str:
    """Read real local target events and automatic Blue Cell incidents."""
    if not 1 <= limit <= 100:
        return json.dumps({"error": "limit must be between 1 and 100"})
    sensor = _url("BLUE_SENSOR_URL", "http://127.0.0.1:18081")
    monitor = _url("BLUE_MONITOR_URL", "http://127.0.0.1:18085")
    try:
        with httpx.Client(timeout=10) as client:
            metrics = client.get(f"{sensor}/metrics")
            metrics.raise_for_status()
            latest = int(metrics.json()["latest_seq"])
            events = client.get(
                f"{sensor}/events", params={"after": max(0, latest - limit), "limit": limit}
            )
            events.raise_for_status()
            incidents = client.get(f"{monitor}/incidents", params={"limit": limit})
            incidents.raise_for_status()
            return json.dumps(
                {
                    "sensor_metrics": metrics.json(),
                    "events": events.json()["events"],
                    "incidents": incidents.json()["incidents"],
                },
                separators=(",", ":"),
            )
    except (httpx.HTTPError, KeyError, ValueError) as error:
        return json.dumps({"error": str(error), "sensor_url": sensor, "monitor_url": monitor})


@tool
def blue_sensor_body(ref: str, max_bytes: int = 65536) -> str:
    """Inspect a bounded preview of a request body captured by the local target sensor."""
    if not re.fullmatch(r"[0-9a-f]{32}", ref):
        return json.dumps({"error": "invalid body reference"})
    if not 1 <= max_bytes <= 262144:
        return json.dumps({"error": "max_bytes must be between 1 and 262144"})
    sensor = _url("BLUE_SENSOR_URL", "http://127.0.0.1:18081")
    try:
        with httpx.Client(timeout=30) as client:
            with client.stream("GET", f"{sensor}/bodies/{ref}") as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length", "0"))
                chunks = []
                remaining = max_bytes
                for chunk in response.iter_bytes():
                    chunks.append(chunk[:remaining])
                    remaining -= min(len(chunk), remaining)
                    if remaining == 0:
                        break
        raw = b"".join(chunks)
        return json.dumps(
            {
                "ref": ref,
                "total_bytes": total,
                "preview_bytes": len(raw),
                "preview_utf8": raw.decode("utf-8", "replace"),
                "preview_limited": total > len(raw),
            },
            separators=(",", ":"),
        )
    except (httpx.HTTPError, ValueError) as error:
        return json.dumps({"error": str(error), "ref": ref})


@tool
def blue_sensor_events(after: int, limit: int = 20) -> str:
    """Read target telemetry after an exact receiver sequence for incident correlation."""
    if after < 0 or not 1 <= limit <= 100:
        return json.dumps({"error": "after must be nonnegative and limit 1-100"})
    sensor = _url("BLUE_SENSOR_URL", "http://127.0.0.1:18081")
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{sensor}/events", params={"after": after, "limit": limit})
            response.raise_for_status()
            return json.dumps(response.json(), separators=(",", ":"))
    except (httpx.HTTPError, ValueError) as error:
        return json.dumps({"error": str(error), "sensor_url": sensor})
