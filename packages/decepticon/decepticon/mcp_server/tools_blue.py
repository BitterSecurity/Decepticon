from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from decepticon.mcp_server.blue import BlueClient


def _limit(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def register_blue_tools(mcp: FastMCP, blue: BlueClient) -> None:
    read_only = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_status() -> dict[str, Any]:
        """Read live sensor and monitor metrics, including collection and watch state."""
        sensor = await blue.get("/metrics")
        monitor = await blue.get("/metrics", monitor=True)
        return {"sensor": sensor, "monitor": monitor}

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_sources(limit: int = 20) -> dict[str, Any]:
        """List target log and ingress sources that actually delivered events."""
        return await blue.get("/sources", params={"limit": _limit(limit)})

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_events(after: int = 0, limit: int = 20) -> dict[str, Any]:
        """Read target events after an exact receiver sequence; retain the last seq as cursor."""
        if after < 0:
            raise ValueError("after must be nonnegative")
        return await blue.get("/events", params={"after": after, "limit": _limit(limit)})

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_incidents(limit: int = 20) -> dict[str, Any]:
        """Read incidents produced by the standing Blue Cell monitor."""
        return await blue.get("/incidents", monitor=True, params={"limit": _limit(limit)})

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_notifications(after: int = 0, limit: int = 20) -> dict[str, Any]:
        """Read defensive messages after a notification sequence; retain next_after."""
        if after < 0:
            raise ValueError("after must be nonnegative")
        return await blue.get(
            "/notifications", monitor=True, params={"after": after, "limit": _limit(limit)}
        )

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_body(ref: str, max_bytes: int = 65536) -> dict[str, Any]:
        """Read a bounded preview of a captured HTTP request body by reference."""
        if not re.fullmatch(r"[0-9a-f]{32}", ref):
            raise ValueError("ref must be a 32-character lowercase hex body reference")
        if not 1 <= max_bytes <= 262144:
            raise ValueError("max_bytes must be between 1 and 262144")
        return await blue.body(ref, max_bytes=max_bytes)

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_search(
        field: str, value: str, limit: int = 20, before: int | None = None
    ) -> dict[str, Any]:
        """Find target evidence by exact request_id, trace_id, or source."""
        if field not in {"request_id", "trace_id", "source"} or not 1 <= len(value) <= 256:
            raise ValueError("field must be request_id, trace_id, or source; value 1-256 chars")
        if before is not None and before < 1:
            raise ValueError("before must be a positive sequence")
        params: dict[str, Any] = {"field": field, "value": value, "limit": _limit(limit)}
        if before is not None:
            params["before"] = before
        return await blue.get("/search", params=params)

    @mcp.tool(annotations=read_only)
    async def decepticon_blue_timeline(
        start_at: str,
        end_at: str,
        source: str | None = None,
        limit: int = 50,
        before: int | None = None,
    ) -> dict[str, Any]:
        """Read target events in a one-hour receiver-time window."""
        try:
            start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
            if start.tzinfo is None or end.tzinfo is None:
                raise ValueError
            if not 0 < (end - start).total_seconds() <= 3600:
                raise ValueError
        except ValueError as error:
            raise ValueError("start_at and end_at need timezones and a window <= 1 hour") from error
        if source is not None and source not in {"blue-ingress-proxy", "target-log-file"}:
            raise ValueError("source must be blue-ingress-proxy or target-log-file")
        if before is not None and before < 1:
            raise ValueError("before must be a positive sequence")
        params: dict[str, Any] = {"start_at": start_at, "end_at": end_at, "limit": _limit(limit)}
        if source is not None:
            params["source"] = source
        if before is not None:
            params["before"] = before
        return await blue.get("/timeline", params=params)
