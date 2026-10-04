from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

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
            source_response = client.get(f"{sensor}/sources", params={"limit": min(limit, 20)})
            if source_response.status_code == 404:
                sources = []
                sources_available = False
            else:
                source_response.raise_for_status()
                sources = source_response.json()["sources"]
                sources_available = True
            return json.dumps(
                {
                    "sensor_metrics": metrics.json(),
                    "sources": sources,
                    "sources_available": sources_available,
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


@tool
def blue_sensor_search(field: str, value: str, limit: int = 20, before: int | None = None) -> str:
    """Find target evidence by exact request_id, trace_id, or trusted source."""
    if field not in {"request_id", "trace_id", "source"} or not 1 <= len(value) <= 256:
        return json.dumps(
            {"error": "field must be request_id, trace_id, or source; value 1-256 chars"}
        )
    if not 1 <= limit <= 100 or (before is not None and before < 1):
        return json.dumps({"error": "limit must be 1-100 and before a positive sequence"})
    sensor = _url("BLUE_SENSOR_URL", "http://127.0.0.1:18081")
    params: dict[str, str | int] = {"field": field, "value": value, "limit": limit}
    if before is not None:
        params["before"] = before
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{sensor}/search", params=params)
            response.raise_for_status()
            return json.dumps(response.json(), separators=(",", ":"))
    except (httpx.HTTPError, ValueError) as error:
        return json.dumps({"error": str(error), "sensor_url": sensor})


@tool
def blue_sensor_timeline(
    start_at: str,
    end_at: str,
    source: str | None = None,
    limit: int = 50,
    before: int | None = None,
) -> str:
    """Read target events in a bounded receiver-time window for incident correlation."""
    try:
        start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError
        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        if not 0 < (end - start).total_seconds() <= 3600:
            raise ValueError
    except ValueError:
        return json.dumps(
            {"error": "start_at and end_at need a timezone and a window of at most one hour"}
        )
    if source is not None and source not in {"blue-ingress-proxy", "target-log-file"}:
        return json.dumps({"error": "invalid source"})
    if not 1 <= limit <= 100 or (before is not None and before < 1):
        return json.dumps({"error": "limit must be 1-100 and before a positive sequence"})
    sensor = _url("BLUE_SENSOR_URL", "http://127.0.0.1:18081")
    params: dict[str, str | int] = {
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "limit": limit,
    }
    if source is not None:
        params["source"] = source
    if before is not None:
        params["before"] = before
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{sensor}/timeline", params=params)
            response.raise_for_status()
            return json.dumps(response.json(), separators=(",", ":"))
    except (httpx.HTTPError, ValueError) as error:
        return json.dumps({"error": str(error), "sensor_url": sensor})
